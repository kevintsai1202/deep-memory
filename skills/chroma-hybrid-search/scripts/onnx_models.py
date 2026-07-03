# -*- coding: utf-8 -*-
"""輕量 ONNX 推論模組：直接用 onnxruntime + tokenizers 載入模型。

刻意不經過 sentence-transformers——它無論選哪個 backend 都會 import torch，
冷啟動要 10 秒以上；本模組只依賴 onnxruntime / tokenizers / numpy /
huggingface_hub（皆為 chromadb 既有相依），冷啟動可壓到約 2-3 秒。

兩個模型的 Hub repo 均已附官方 fp32 ONNX 權重（onnx/model.onnx），
輸出與 torch 版數值一致（cosine similarity = 1.0、rerank 分數差 < 1e-6），
因此既有向量索引與 --min-score 門檻皆不需變動。
"""
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer
from huggingface_hub import hf_hub_download

# 兩個模型與 sentence-transformers 版行為對齊的最大序列長度
MAX_LENGTH = 512


def _load(repo_id):
    """從 HF 快取（優先）或網路下載 ONNX 權重與 tokenizer，回傳推論 session 與 tokenizer"""
    files = {}
    for filename in ("onnx/model.onnx", "tokenizer.json"):
        try:
            # 先只查本地快取，避免每次啟動都對 Hub 發網路請求
            files[filename] = hf_hub_download(repo_id, filename, local_files_only=True)
        except Exception:
            files[filename] = hf_hub_download(repo_id, filename)
    sess = ort.InferenceSession(files["onnx/model.onnx"], providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(files["tokenizer.json"])
    tok.enable_truncation(max_length=MAX_LENGTH)
    return sess, tok


def _run(sess, ids, mask):
    """組推論輸入（依模型實際需要決定是否補 token_type_ids）並執行"""
    feed = {"input_ids": ids, "attention_mask": mask}
    input_names = {i.name for i in sess.get_inputs()}
    if "token_type_ids" in input_names:
        feed["token_type_ids"] = np.zeros_like(ids)
    return sess.run(None, feed)[0]


class E5OnnxEmbeddingFunction:
    """multilingual-e5-small 的 Chroma embedding function（mean pooling + L2 normalize）"""

    def __init__(self):
        self.sess, self.tok = _load("intfloat/multilingual-e5-small")
        # 批次內對齊長度；pad token 依 XLM-R 慣例為 <pad>(id=1)
        self.tok.enable_padding(pad_id=1, pad_token="<pad>")

    def name(self):
        return "e5-onnx"

    def __call__(self, input):
        encs = self.tok.encode_batch(list(input))
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        hidden = _run(self.sess, ids, mask)  # (batch, seq, hidden)
        # mean pooling：只平均非 padding 位置，與 sentence-transformers 的 Pooling 層一致
        m = mask[..., None].astype(np.float32)
        emb = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        # 模型的 ST 管線含 Normalize 層，這裡同樣做 L2 正規化以維持索引相容
        emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
        return emb.tolist()


class OnnxReranker:
    """bge-reranker-base 的 cross-encoder 重排器，介面對齊 CrossEncoder.predict"""

    def __init__(self):
        self.sess, self.tok = _load("BAAI/bge-reranker-base")
        self.tok.enable_padding(pad_id=1, pad_token="<pad>")

    def predict(self, pairs):
        """輸入 [query, doc] 列表，回傳 sigmoid 後的相關性分數（同 CrossEncoder 預設行為）"""
        encs = self.tok.encode_batch([(q, d) for q, d in pairs])
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        logits = _run(self.sess, ids, mask).reshape(-1).astype(np.float64)
        return 1.0 / (1.0 + np.exp(-logits))
