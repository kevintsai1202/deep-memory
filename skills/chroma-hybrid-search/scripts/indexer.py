# -*- coding: utf-8 -*-
"""
indexer.py
向量索引的 schema 偵測、遷移、增量比對、寫入與執行日誌。

從 update_db.py 抽出，讓 search.py 能共用同一套增量邏輯做自動補索引，
避免兩邊各寫一份而發散——兩邊對「什麼算變動」的認定必須永遠一致。
"""
import os

# 索引 schema 版本。變更 ID 格式或 metadata 必要欄位時才遞增。
SCHEMA_VERSION = 2

# 舊版冷庫 ID 前綴（行號定址）。存在此前綴即代表該庫尚未遷移。
LEGACY_COLD_PREFIX = "cold-notes/raw.jsonl#L"


def detect_schema(collection):
    """判斷向量庫的 schema 版本，決定要不要遷移。

    以「是否存在行號式 ID」與「metadata 的 schema_version」兩個訊號綜合判定，
    並取最舊的版本——遷移中斷造成的混合狀態必須被判為需要遷移，
    不能因為抽樣抽到已遷移的那筆就放過。
    """
    ids = collection.get(include=[])["ids"]
    if not ids:
        return SCHEMA_VERSION
    if any(i.startswith(LEGACY_COLD_PREFIX) for i in ids):
        return 1
    metas = collection.get(include=["metadatas"])["metadatas"]
    versions = {(m or {}).get("schema_version", 1) for m in metas}
    return min(versions) if versions else SCHEMA_VERSION


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


def build_plan(collection, docs):
    """比對來源文件與向量庫現況，產出增量計畫。

    只讀 metadatas 不讀 documents：比對鍵是 content_hash，
    毋須把整個知識庫的全文（數十 MB）搬進記憶體。
    """
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
