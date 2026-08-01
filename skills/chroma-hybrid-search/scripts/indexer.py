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
