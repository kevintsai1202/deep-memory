# -*- coding: utf-8 -*-
"""
indexer.py
向量索引的 schema 偵測、遷移、增量比對、寫入與執行日誌。

從 update_db.py 抽出，讓 search.py 能共用同一套增量邏輯做自動補索引，
避免兩邊各寫一份而發散——兩邊對「什麼算變動」的認定必須永遠一致。
"""
import json
import os
from datetime import datetime

# 索引 schema 版本。變更 ID 格式或 metadata 必要欄位時才遞增。
SCHEMA_VERSION = 2

# 舊版冷庫 ID 前綴（行號定址）。存在此前綴即代表該庫尚未遷移。
LEGACY_COLD_PREFIX = "cold-notes/raw.jsonl#L"

# 冷庫 ID 的共同前綴，同時涵蓋舊版行號式（cold-notes/raw.jsonl#L{n}）與
# 新版內容定址（cold-notes#<hash>）兩種形式。用來判斷「這是冷庫條目」，
# 例如 backfill_hot_entries 得知道哪些 ID 不能碰。
COLD_ID_PREFIX = "cold-notes"


def detect_schema(collection, existing=None):
    """判斷向量庫的 schema 版本，決定要不要遷移。

    以「是否存在行號式 ID」與「metadata 的 schema_version」兩個訊號綜合判定，
    並取最舊的版本——遷移中斷造成的混合狀態必須被判為需要遷移，
    不能因為抽樣抽到已遷移的那筆就放過。
    existing 為 sync() 預先取得的 collection.get(include=["metadatas"]) 快照，
    未傳入時才自行讀取；數千筆規模下每次讀取約 2 秒，不該重複付。
    """
    if existing is None:
        existing = collection.get(include=["metadatas"])
    ids = existing["ids"]
    if not ids:
        return SCHEMA_VERSION
    if any(i.startswith(LEGACY_COLD_PREFIX) for i in ids):
        return 1
    metas = existing["metadatas"]
    versions = {(m or {}).get("schema_version", 1) for m in metas}
    return min(versions) if versions else SCHEMA_VERSION


def assert_schema_supported(collection, existing=None):
    """庫的 schema 版本高於本腳本時中止執行。

    舊腳本寫進新格式的庫會產生無法自動修復的混合狀態——
    寧可明確擋下並要求升級 skill，也不要留下一個壞掉的索引。
    existing 同 detect_schema：可傳入預取快照以免重複讀取。
    """
    if existing is None:
        existing = collection.get(include=["metadatas"])
    metas = existing["metadatas"]
    versions = {(m or {}).get("schema_version", 1) for m in metas}
    if versions and max(versions) > SCHEMA_VERSION:
        raise RuntimeError(
            f"向量庫 schema 版本為 {max(versions)}，高於本腳本的 {SCHEMA_VERSION}。"
            f"請先升級 chroma-hybrid-search 技能（repo 與 ~/.claude/skills 兩處都要），"
            f"再重新執行。")


def build_metadata(doc):
    """把 kb_reader 的 doc 轉為 Chroma metadata。

    Chroma 不接受 None 與空陣列，因此無值欄位一律整個省略而非填預設值——
    這也讓 search.py 能區分「沒有 project 的舊條目」與「屬於某專案的新條目」。
    """
    meta = {
        "path": doc["path"],
        "source": doc.get("source", "hot"),
        "content_hash": doc["content_hash"],
        "schema_version": SCHEMA_VERSION,
    }
    for key in ("skill", "memory_type", "project"):
        if doc.get(key):
            meta[key] = doc[key]
    if doc.get("tags"):
        meta["tags"] = doc["tags"]
    if doc.get("line") is not None:
        meta["line"] = doc["line"]
    return meta


class IndexPlan:
    """一次索引執行的待辦清單：要重算的、只更新 metadata 的、要刪除的。

    先算出完整計畫再執行，好處是能在動用 embedding 模型之前就知道規模，
    據此決定要不要載入模型、要不要發出異常警告。
    """

    def __init__(self):
        self.embed_ids, self.embed_docs, self.embed_metas = [], [], []
        self.meta_ids, self.meta_metas = [], []
        self.stale_ids = []
        self.skipped = 0
        # 重算原因分類，寫入日誌供事後判讀「這次為什麼慢」
        self.reasons = {"new": 0, "text-changed": 0, "id-missing": 0}

    @property
    def is_empty(self):
        return not (self.embed_ids or self.meta_ids or self.stale_ids)


def _backup_db(db_path):
    """遷移前完整複製資料庫目錄，讓任何中斷都能原樣還原。

    27 MB 規模下實測約 0.06 秒，這個保險非常便宜。向量庫本身可從 raw.jsonl／
    knowledge-base／experience 完全重建（遷移全程不觸碰這些來源檔），所以備份
    防的不是資料遺失，而是讓中斷後的復原不必走「當新條目重新計算 embedding」
    的慢路徑（1263 筆約 6.4 分鐘）。
    db_path 為 None 時（記憶體測試）跳過；db_path 有給值但不是目錄時 raise，
    不能悄悄跳過——路徑打錯或資料庫還沒建立時，讓這個便宜的保險無聲消失沒有
    任何好處，只會讓中斷後的排查更麻煩。
    """
    if db_path is None:
        return None
    if not os.path.isdir(db_path):
        raise RuntimeError(f"[遷移] 備份失敗：db_path 不是目錄（收到：{db_path}）")
    import shutil
    from datetime import datetime
    dest = f"{db_path}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copytree(db_path, dest)
    return dest


def migrate_to_v2(collection, db_path, cold_docs):
    """把行號定址的冷庫 ID 換成內容定址，並回填 content_hash 與 schema_version。

    關鍵性質是零重算：embedding 只跟文字有關、跟 ID 無關，既有向量原封搬到
    新 ID 即可。實測 910 筆 17.5 秒、0 次推論，相較全庫重建的 6.3 分鐘快 21 倍。

    以「舊文字 → 新 doc」建立對應，因為此時 raw.jsonl 的行號與庫內舊 ID 未必
    仍然對齊（若已有人刪改過行）；用文字比對才不會把向量接到錯的條目上。

    中止條件（以下皆 raise RuntimeError，保留原狀不動）：備份路徑無效
    （db_path 有給值但不是目錄）、配對率低於五成、多筆 legacy 條目配對到同一個
    新 ID（raw.jsonl 語意重複）、upsert 後新 ID 未全數就位。
    """
    legacy_ids = [i for i in collection.get(include=[])["ids"]
                  if i.startswith(LEGACY_COLD_PREFIX)]
    if not legacy_ids:
        return collection

    backup = _backup_db(db_path)
    if backup:
        print(f"[遷移] 已備份向量庫至 {backup}")

    data = collection.get(ids=legacy_ids, include=["embeddings", "documents", "metadatas"])
    by_text = {d["text"]: d for d in cold_docs}

    new_ids, embs, docs_out, metas = [], [], [], []
    for i, old_id in enumerate(data["ids"]):
        target = by_text.get(data["documents"][i])
        if target is None:
            # 來源已刪除的條目：不搬移，其舊 ID 會在下方連同其他 legacy ID 一併刪除。
            # 不保留未配對的舊 ID 是刻意的——留著會讓 detect_schema 永遠停在舊版本，
            # 導致每次 sync 都重新備份並重跑一次遷移。
            continue
        new_ids.append(target["path"])
        embs.append(data["embeddings"][i])
        docs_out.append(target["text"])
        metas.append(build_metadata(target))

    # 未配對有兩種來源：使用者刪除了冷庫記錄（正常，通常是個位數），或冷庫 text
    # 組裝格式漂移（異常，通常是大規模）。兩者無法從單筆判斷，只能從量級區分——
    # 超過一半配不上，幾乎不可能是手動刪除累積出來的比例，實務上必然是格式問題，
    # 這才是門檻定在五成的理由，不是為了配合任何測試案例。
    #
    # 低於門檻的不匹配刻意不中止：這些條目會在 build_plan 被當成新條目重新計算
    # embedding，結果仍然正確、只是較慢（向量庫可從 raw.jsonl／knowledge-base／
    # experience 完全重建，並非遺失資料）；Task 4 的 20% 重算異常警告是第二層
    # 防線，兩層門檻各司其職。
    matched, total = len(new_ids), len(legacy_ids)
    if matched < total * 0.5:
        raise RuntimeError(
            f"[遷移] 配對率過低已中止：legacy 有 {total} 筆、配對成功 {matched} 筆，"
            f"未搬移任何向量；請檢查冷庫 text 組裝格式是否已變動"
            + (f"，備份於 {backup}" if backup else ""))

    # 兩筆以上 legacy 條目配對到同一個新 path，代表 raw.jsonl 本身有語意重複的行
    # （不同行但雜湊相同）；不擋下的話 upsert 會丟出 chromadb 的 DuplicateIDError，
    # 只吐一個裸雜湊值、看不出成因是 raw.jsonl 需要去重
    seen, dupes = set(), set()
    for target_id in new_ids:
        if target_id in seen:
            dupes.add(target_id)
        else:
            seen.add(target_id)
    if dupes:
        raise RuntimeError(
            f"[遷移] 偵測到重複的目標 ID {sorted(dupes)}，代表 raw.jsonl 有語意重複的"
            f"條目；請先在 raw.jsonl 去重再重跑遷移"
            + (f"，備份於 {backup}" if backup else ""))

    for s in range(0, len(new_ids), 500):
        collection.upsert(ids=new_ids[s:s+500], embeddings=embs[s:s+500],
                          documents=docs_out[s:s+500], metadatas=metas[s:s+500])

    # 先驗證新 ID 全數就位再刪舊的，任何不符就中止並保留原狀
    landed = set(collection.get(ids=new_ids, include=[])["ids"])
    if landed != set(new_ids):
        raise RuntimeError(
            f"[遷移] 新 ID 未全數就位（{len(landed)} != {len(set(new_ids))}），"
            f"已中止且未刪除舊 ID；如需還原請使用備份 {backup}")

    for s in range(0, len(legacy_ids), 500):
        collection.delete(ids=legacy_ids[s:s+500])

    print(f"[遷移] 冷庫 ID 改為內容定址，共 {len(new_ids)} 筆，未重算任何向量")
    return collection


def backfill_hot_entries(collection):
    """為 v1 熱庫條目回填 content_hash 與 schema_version（零 embedding 成本）。

    起因（2026-08-01 對正式庫遷移時的真實事故）：v1 庫的熱庫 metadata
    只有 memory_type / path / source 三個欄位，沒有 content_hash。
    build_plan 比對新舊 metadata 時，
    `(old or {}).get("content_hash") != doc["content_hash"]` 這一行
    寫在「metadata 不同才走 collection.update()」的分支*之前*執行，
    `None != "<hash>"` 恆為真，於是全部落入 text-changed 分支、
    當成文字被改過重新計算 embedding。那次遷移因此把 337 筆一字未改
    的熱庫條目（庫內既有筆數的 27%）都重算了一次，多花 126 秒，
    還誤觸了 20% 的異常重算警告，讓使用者以為索引出了問題——
    其實只是遷移路徑少做了這一步回填。

    這個函式就是補上那一步：直接對 collection 做 collection.update()，
    把缺少的 content_hash 與 schema_version 疊加回原有 metadata。
    之後 build_plan 再比對同一批熱庫條目時，old.get("content_hash")
    已經等於新雜湊，走的是完全跳過，不會誤判成文字變動。

    只處理熱庫條目（ID 不以 COLD_ID_PREFIX 開頭）：熱庫的 content_hash
    是 hot_entry_hash(entry 全文)，而 collection 存的 document 本身
    就是這段全文，兩者需要的資料都在庫內，可以現場反推。冷庫的
    content_hash 則是 semantic_hash(entry)，由 raw.jsonl 的
    topic/content/tags/skill/memory_type 等「原始」欄位算出；collection
    裡的 document 只是組裝給人看的顯示文字，缺了原始欄位反推不出正確
    雜湊。冷庫條目的 content_hash 只能靠 migrate_to_v2 換址時透過
    build_metadata 補上，不屬於本函式的職責，誤觸只會寫入錯的雜湊，
    讓冷庫條目永遠比對不上而每次都被當成 text-changed。
    """
    from kb_reader import hot_entry_hash

    data = collection.get(include=["documents", "metadatas"])
    fix_ids, fix_metas = [], []
    for doc_id, text, meta in zip(data["ids"], data["documents"], data["metadatas"]):
        if doc_id.startswith(COLD_ID_PREFIX):
            continue
        meta = meta or {}
        if meta.get("content_hash"):
            continue  # 已有 content_hash：不是 v1 遺留條目，不需回填
        # 保留原有欄位、只疊加缺少的兩個 key——不論 collection.update()
        # 對 metadata 是整筆覆蓋還是逐欄合併，這樣寫都不會弄丟既有欄位
        # （例如 memory_type、skill、tags、project）。
        new_meta = dict(meta)
        new_meta["content_hash"] = hot_entry_hash(text)
        new_meta["schema_version"] = SCHEMA_VERSION
        fix_ids.append(doc_id)
        fix_metas.append(new_meta)

    if fix_ids:
        collection.update(ids=fix_ids, metadatas=fix_metas)
        print(f"[遷移] 回填 {len(fix_ids)} 筆熱庫條目的 content_hash，未重算任何向量")
    return len(fix_ids)


def build_plan(collection, docs, existing=None):
    """比對來源文件與向量庫現況，產出增量計畫。

    只讀 metadatas 不讀 documents：比對鍵是 content_hash，
    毋須把整個知識庫的全文（數十 MB）搬進記憶體。
    existing 同 detect_schema：可傳入預取快照以免重複讀取。
    """
    if existing is None:
        existing = collection.get(include=["metadatas"])
    existing_metas = dict(zip(existing["ids"], existing["metadatas"]))

    plan = IndexPlan()
    ids = [d["path"] for d in docs]
    plan.stale_ids = sorted(set(existing_metas) - set(ids))

    for doc in docs:
        doc_id = doc["path"]
        meta = build_metadata(doc)
        old = existing_metas.get(doc_id)

        if old is None:
            plan.embed_ids.append(doc_id)
            plan.embed_docs.append(doc["text"])
            plan.embed_metas.append(meta)
            plan.reasons["new"] += 1
        elif (old or {}).get("content_hash") != doc["content_hash"]:
            plan.embed_ids.append(doc_id)
            plan.embed_docs.append(doc["text"])
            plan.embed_metas.append(meta)
            plan.reasons["text-changed"] += 1
        elif old != meta:
            plan.meta_ids.append(doc_id)
            plan.meta_metas.append(meta)
        else:
            plan.skipped += 1

    # 「新 ID 出現」與「舊 ID 消失」成對發生的部分歸類為 id-missing。
    #
    # 內容定址下，冷庫條目一旦被編輯，其 ID 就會改變——表現正是「舊 ID 孤立
    # ＋ 新 ID 新增」，所以冷庫永遠不會走 text-changed 分支（那隻對 ID 穩定的
    # 熱庫條目發生）。單筆編輯與大規模位移在型態上相同、只差在量級，因此這裡
    # 不設門檻硬分，只如實記錄成對數量，交由 20% 的警告門檻判定是否異常。
    shifted = min(plan.reasons["new"], len(plan.stale_ids))
    plan.reasons["id-missing"] = shifted
    plan.reasons["new"] -= shifted

    return plan


# 單次重算超過庫內既有筆數的此比例即視為異常，主動提示而非默默跑完
ANOMALY_RATIO = 0.2


def write_run_log(base_dir, record):
    """把一次索引執行追加為一行 JSON 到 logs/index-runs.jsonl。

    採 JSONL 而非人類可讀格式，是為了讓「這半年索引變慢過幾次、各是什麼原因」
    能直接用程式彙總。單行約 300 bytes，每日十次執行一年約 1 MB，不需輪替。

    I/O 失敗時吞掉例外、印警告、回傳 None：日誌的目的是觀測，觀測工具的失敗
    （例如 Windows 檔案鎖或防毒短暫佔用）不該破壞它要觀測的東西（已完成的索引）。
    """
    try:
        log_dir = os.path.join(str(base_dir), "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "index-runs.jsonl")
        payload = {"ts": datetime.now().isoformat(timespec="seconds"),
                   "schema_version": SCHEMA_VERSION}
        payload.update(record)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path
    except OSError as e:
        print(f"[警告] 索引日誌寫入失敗，但索引本身已完成：{e}")
        return None


def format_anomaly_warning(plan, total):
    """重算規模異常時產生警告文字，沒有異常則回傳 None。

    total 為庫內既有筆數：首次建庫（total=0）全是新條目屬正常，不該報警。
    直接把原因分佈印在 console，使用者不必事後翻日誌才知道自己在等什麼。
    """
    if total <= 0 or not plan.embed_ids:
        return None
    ratio = len(plan.embed_ids) / total
    if ratio < ANOMALY_RATIO:
        return None

    reasons = ", ".join(f"{k}={v}" for k, v in plan.reasons.items() if v)
    lines = [
        f"[⚠️ 異常] 本次需重算 {len(plan.embed_ids)} 筆，佔庫內 {total} 筆的 {ratio:.0%}。",
        f"  重算原因分佈：{reasons}",
    ]
    if plan.reasons.get("id-missing"):
        lines.append("  出現 id-missing：通常代表 ID 位移，或執行中的腳本版本與"
                     "建立索引時不同（例如 repo 版與 ~/.claude/skills 安裝副本不一致）。")
        lines.append("  請先確認兩處的 kb_reader.py 一致再重跑。")
    lines.append(f"  樣本：{', '.join(plan.embed_ids[:3])}")
    return "\n".join(lines)


def sync(base_dir, trigger="update_db", rebuild=False, verbose=True, return_docs=False):
    """執行一次完整的索引同步，回傳統計結果。

    trigger 記錄呼叫來源（update_db / search-auto），寫進日誌以便區分
    「使用者手動跑」與「搜尋時自動補」兩種情境的耗時分佈。
    rebuild=True 時強制重算全部條目（遷移後想讓向量與新文字格式完全對應時使用）。

    回傳 dict 的 backfilled 欄位記錄本次 backfill_hot_entries 實際回填的
    熱庫筆數：這跟 reasons 一樣是診斷訊號，寫進 index-runs.jsonl 才能在
    事後追溯「這次遷移到底補了多少筆」，不必只靠 console 輸出。

    return_docs=True 時回傳 (result, docs)，讓 search.py 直接複用這次讀到的
    語料，不必再讀一次全部 MD 與 raw.jsonl。

    collection.get(include=["metadatas"]) 整個流程只讀一次快照（遷移時例外，
    遷移改了 ID 與 metadata，完成後要重讀），由 assert_schema_supported、
    detect_schema、build_plan 與筆數統計共用。
    """
    import time
    import chromadb
    from kb_reader import read_knowledge_base, read_cold_notes

    timing = {}
    t0 = time.time()

    base_dir = os.path.abspath(str(base_dir))
    db_path = os.path.join(base_dir, "chroma_hybrid_db")

    t = time.time()
    hot_docs = read_knowledge_base(base_dir)
    cold_docs = read_cold_notes(base_dir)
    docs = hot_docs + cold_docs
    timing["read"] = round(time.time() - t, 2)

    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection("hybrid_docs")
    t = time.time()
    # 本次同步共用的庫內快照（ids + metadatas）
    existing = collection.get(include=["metadatas"])
    assert_schema_supported(collection, existing=existing)

    # 這個 if 底下做兩件事：migrate_to_v2 把冷庫的行號式 ID 換成內容定址；
    # backfill_hot_entries 用 collection.update() 補上熱庫條目缺少的
    # content_hash 與 schema_version（零 embedding 成本）。後者不是可有
    # 可無的收尾，而是必要的一步：v1 庫的熱庫 metadata 只有
    # memory_type / path / source，沒有 content_hash。build_plan 比對時
    # `(old or {}).get("content_hash") != doc["content_hash"]` 這一行
    # 先於「metadata 不同才 update」的分支執行，`None != "<hash>"` 恆為
    # 真——若不先補上 content_hash，所有熱庫條目都會被誤判為
    # text-changed 而重新計算 embedding。2026-08-01 對正式庫遷移時就是
    # 這樣：一字未改的 337 筆熱庫條目被重算，多花 126 秒，還誤觸了 20%
    # （337/1247≈27%）的異常重算警告，讓使用者誤以為出了問題。
    #
    # migrate_to_v2 在「已無行號式冷庫 ID」時會提早 return（可能是冷庫
    # 已遷移過、只剩熱庫尚未回填 content_hash 的部分遷移狀態），所以
    # backfill_hot_entries 不能掛在它內部，必須留在這個 if 區塊裡與它
    # 平行呼叫，兩者才會各自在需要時都執行到。等所有條目的
    # schema_version 皆為 SCHEMA_VERSION 後，detect_schema 才會回報
    # 最新版本，本區塊屆時就不會再被進入。
    backfilled = 0
    if detect_schema(collection, existing=existing) < SCHEMA_VERSION:
        t_mig = time.time()
        # 遷移要用含 reviewed 的全量冷庫配對舊向量：read_cold_notes 預設略過
        # reviewed，精煉過的舊庫 reviewed 常佔九成，只拿過濾後的清單配對會
        # 跌破五成門檻而中止。配上的 reviewed 向量隨後由 build_plan 當孤立向量清掉。
        all_cold = read_cold_notes(base_dir, include_reviewed=True)
        collection = migrate_to_v2(collection, db_path, all_cold)
        backfilled = backfill_hot_entries(collection)
        timing["migrate"] = round(time.time() - t_mig, 2)
        # 遷移改變了庫內 ID 與 metadata，重新取一次快照
        t = time.time()
        existing = collection.get(include=["metadatas"])

    plan = build_plan(collection, docs, existing=existing)
    timing["get"] = round(time.time() - t, 2)

    total_existing = len(existing["ids"])

    if rebuild:
        # 強制全量：把所有條目都放進待重算清單
        plan.embed_ids = [d["path"] for d in docs]
        plan.embed_docs = [d["text"] for d in docs]
        plan.embed_metas = [build_metadata(d) for d in docs]
        plan.meta_ids, plan.meta_metas = [], []
        plan.skipped = 0

    warning = None if rebuild else format_anomaly_warning(plan, total_existing)
    if warning and verbose:
        print(warning)

    if plan.stale_ids:
        # 分批刪除：首次啟用 reviewed 過濾時會一次清掉數千筆，可能超過 chromadb 單批上限
        for s in range(0, len(plan.stale_ids), 500):
            collection.delete(ids=plan.stale_ids[s:s+500])
        if verbose:
            print(f"[清理] 移除 {len(plan.stale_ids)} 筆孤立向量（來源已刪除或改名）")

    timing["model_load"] = 0.0
    timing["embed"] = 0.0
    if plan.embed_ids:
        # 到這裡才載入模型：零變動或 metadata-only 的執行完全不付模型載入成本
        t = time.time()
        # 取同行程共用實例：由 search.py 觸發時，查詢向量接著沿用同一個模型
        import onnx_models
        ef = onnx_models.get_shared_e5()
        timing["model_load"] = round(time.time() - t, 2)

        t = time.time()
        embeddings = ef(plan.embed_docs)
        timing["embed"] = round(time.time() - t, 2)

        collection.upsert(ids=plan.embed_ids, embeddings=embeddings,
                          documents=plan.embed_docs, metadatas=plan.embed_metas)
    if plan.meta_ids:
        collection.update(ids=plan.meta_ids, metadatas=plan.meta_metas)

    timing["total"] = round(time.time() - t0, 2)
    result = {
        "trigger": trigger,
        "total": len(docs),
        "embedded": len(plan.embed_ids),
        "meta_only": len(plan.meta_ids),
        "skipped": plan.skipped,
        "stale": len(plan.stale_ids),
        # 回填筆數是跟 text-changed 同一類的診斷訊號——當初 337 筆熱庫被
        # 誤重算，正是靠 index-runs.jsonl 裡的 reasons 才被發現的；回填
        # 筆數若只印在 console 就消失，同一類問題下次會更難從日誌重建現場。
        "backfilled": backfilled,
        "reasons": plan.reasons,
        "timing": timing,
    }
    write_run_log(base_dir, result)

    if verbose:
        print(f"[增量] 重算 {result['embedded']} 筆、僅更新 metadata "
              f"{result['meta_only']} 筆、未變跳過 {result['skipped']} 筆"
              f"（耗時 {timing['total']}s）")
    return (result, docs) if return_docs else result


def ensure_fresh(base_dir, verbose=False, return_docs=False):
    """供 search.py 於查詢前呼叫的自動補索引。

    索引是為了服務檢索，不該反過來讓檢索失效——任何失敗都只印警告後放行，
    使用者最多少撈到最新幾筆，而不是整個搜尋不能用。
    return_docs=True 時回傳 (result, docs) 供呼叫端複用已讀取的語料；
    失敗時回傳 (None, None)，呼叫端見 docs 為 None 就自行讀檔。
    """
    try:
        return sync(base_dir, trigger="search-auto", verbose=verbose, return_docs=return_docs)
    except Exception as exc:
        print(f"[WARN] 自動補索引失敗，改用現有索引繼續查詢：{exc}")
        return (None, None) if return_docs else None
