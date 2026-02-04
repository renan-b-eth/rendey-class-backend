import math
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+", re.UNICODE)


_STOPWORDS_PT = {
    "a",
    "o",
    "os",
    "as",
    "um",
    "uma",
    "uns",
    "umas",
    "de",
    "do",
    "da",
    "dos",
    "das",
    "e",
    "é",
    "em",
    "no",
    "na",
    "nos",
    "nas",
    "para",
    "por",
    "com",
    "sem",
    "que",
    "se",
    "ao",
    "à",
    "às",
    "ou",
    "como",
    "mais",
    "menos",
    "muito",
    "muita",
    "muitos",
    "muitas",
    "também",
    "já",
    "não",
    "sim",
    "sobre",
    "entre",
    "até",
    "sua",
    "seu",
    "suas",
    "seus",
}


@dataclass
class RetrievedChunk:
    source: str
    score: float
    text: str
    index: int


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    tokens = [t.lower() for t in _WORD_RE.findall(text)]
    return [t for t in tokens if len(t) >= 2 and t not in _STOPWORDS_PT]


def chunk_text(text: str, *, chunk_chars: int = 1400, overlap: int = 200) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []

    chunk_chars = max(300, int(chunk_chars))
    overlap = max(0, min(int(overlap), chunk_chars - 50))

    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) <= 1:
        paras = [t]

    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0

    def flush():
        nonlocal cur, cur_len
        if not cur:
            return
        ch = "\n\n".join(cur).strip()
        if ch:
            chunks.append(ch)
        cur = []
        cur_len = 0

    for p in paras:
        if cur_len + len(p) + 2 > chunk_chars and cur:
            flush()
        cur.append(p)
        cur_len += len(p) + 2
        if cur_len >= chunk_chars:
            flush()

    flush()

    if overlap > 0 and len(chunks) > 1:
        overlapped: List[str] = []
        tail = ""
        for ch in chunks:
            if tail:
                overlapped.append((tail + "\n\n" + ch).strip())
            else:
                overlapped.append(ch)
            tail = ch[-overlap:]
        return overlapped

    return chunks


def _tf(tokens: Sequence[str]) -> Dict[str, float]:
    if not tokens:
        return {}
    counts: Dict[str, int] = {}
    for tok in tokens:
        counts[tok] = counts.get(tok, 0) + 1
    total = float(len(tokens))
    return {k: v / total for k, v in counts.items()}


def _idf(docs: Sequence[Sequence[str]]) -> Dict[str, float]:
    df: Dict[str, int] = {}
    for d in docs:
        seen = set(d)
        for tok in seen:
            df[tok] = df.get(tok, 0) + 1

    n = float(max(1, len(docs)))
    out: Dict[str, float] = {}
    for tok, freq in df.items():
        out[tok] = math.log((n + 1.0) / (float(freq) + 1.0)) + 1.0
    return out


def _cosine_sim(tf_q: Dict[str, float], tf_d: Dict[str, float], idf: Dict[str, float]) -> float:
    if not tf_q or not tf_d:
        return 0.0

    dot = 0.0
    nq = 0.0
    nd = 0.0

    for tok, wq in tf_q.items():
        w = wq * idf.get(tok, 0.0)
        nq += w * w

    for tok, wd in tf_d.items():
        w = wd * idf.get(tok, 0.0)
        nd += w * w

    if nq <= 0.0 or nd <= 0.0:
        return 0.0

    for tok, wq in tf_q.items():
        if tok not in tf_d:
            continue
        dot += (wq * idf.get(tok, 0.0)) * (tf_d[tok] * idf.get(tok, 0.0))

    return dot / (math.sqrt(nq) * math.sqrt(nd))


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa.intersection(sb))
    uni = len(sa.union(sb))
    return float(inter) / float(max(1, uni))


def _signature(tokens: Sequence[str], top: int = 24) -> List[str]:
    if not tokens:
        return []
    counts: Dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[: max(6, int(top))]]


def retrieve_top_chunks(
    *,
    query: str,
    sources: Sequence[Tuple],
    top_k: int = 6,
    chunk_chars: int = 1400,
    overlap: int = 200,
    min_score: float = 0.03,
    dedupe_threshold: float = 0.86,
) -> List[RetrievedChunk]:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []

    all_chunks: List[Tuple[str, int, str, float]] = []
    for s in sources:
        source_name = str(s[0])
        text = s[1] if len(s) > 1 else None
        weight = float(s[2]) if len(s) > 2 and s[2] is not None else 1.0
        for idx, ch in enumerate(chunk_text(text or "", chunk_chars=chunk_chars, overlap=overlap)):
            all_chunks.append((source_name, idx, ch, weight))

    if not all_chunks:
        return []

    doc_tokens = [_tokenize(ch) for _, _, ch, _ in all_chunks]
    idf = _idf(doc_tokens)

    tf_q = _tf(q_tokens)
    scored: List[RetrievedChunk] = []
    for (source_name, idx, ch, weight), toks in zip(all_chunks, doc_tokens):
        tf_d = _tf(toks)
        score = _cosine_sim(tf_q, tf_d, idf) * float(weight)
        if score >= float(min_score):
            scored.append(RetrievedChunk(source=source_name, score=score, text=ch, index=idx))

    scored.sort(key=lambda c: c.score, reverse=True)

    selected: List[RetrievedChunk] = []
    sigs: List[List[str]] = []
    for ch in scored:
        toks = _tokenize(ch.text)
        sig = _signature(toks)
        is_dup = any(_jaccard(sig, s) >= float(dedupe_threshold) for s in sigs if s)
        if is_dup:
            continue
        selected.append(ch)
        sigs.append(sig)
        if len(selected) >= max(1, int(top_k)):
            break

    return selected


def format_retrieved_context(chunks: Sequence[RetrievedChunk], *, max_chars: int = 9000) -> str:
    if not chunks:
        return ""

    parts: List[str] = []
    used = 0
    for i, ch in enumerate(chunks, start=1):
        header = f"[TRECHO {i} • {ch.source} • score {ch.score:.2f}]\n"
        body = ch.text.strip()
        block = header + body
        if used + len(block) > int(max_chars):
            remaining = int(max_chars) - used - len(header)
            if remaining <= 0:
                break
            body = body[:remaining].rstrip()
            block = header + body
            parts.append(block)
            used += len(block)
            break
        parts.append(block)
        used += len(block) + 2

    return "\n\n".join(parts).strip()
