"""Lightweight local error-gate: run the fully-local pipeline over the CRAG test
questions and report answer-match + citation quality. Decides whether the base
local generator warrants fine-tuning (P3), before investing in LoRA.

Scoring mirrors the offline scorer's normalize/_contains_phrase for answer match;
it is a proxy, not the full support+citation gate. Usage:
    python -m comparison.local_eval --base http://127.0.0.1:8001
"""
from __future__ import annotations
import argparse, json, re, uuid
from pathlib import Path
import httpx
from comparison.benchmark.score import normalize, _contains_phrase

CITE = re.compile(r"\[([A-Za-z0-9_.-]+::c\d+)\]")
ROOT = Path(__file__).resolve().parents[1]


def answer_matches(answer: str, gold: dict) -> bool:
    na = normalize(answer)
    cands = [gold.get("answer", "")] + list(gold.get("alt_answers", []))
    return any(_contains_phrase(na, normalize(c)) for c in cands if c)


def drive(base: str, question: str, query_time: str) -> str:
    tid = str(uuid.uuid4()); sess = "eval-" + uuid.uuid4().hex[:8]
    with httpx.Client(timeout=120) as c:
        r = c.post(f"{base}/v1/turns/{tid}/commit",
                   json={"session_id": sess, "revision": 1, "text": question, "query_time": query_time})
        r.raise_for_status(); run = r.json()["run_id"]
        parts = []
        with c.stream("GET", f"{base}/v1/runs/{run}/events") as s:
            for line in s.iter_lines():
                if not line or not line.startswith("data:"): continue
                try: ev = json.loads(line[5:].strip())
                except Exception: continue
                if ev.get("type") == "answer.delta": parts.append(ev.get("text", ""))
                elif ev.get("type") in ("agent.persisted", "run.completed"): break
    return "".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--queries", default=str(ROOT / "data/crag_eval/test_queries.jsonl"))
    ap.add_argument("--gold", default=str(ROOT / "data/crag_eval/test_gold.jsonl"))
    args = ap.parse_args()
    queries = [json.loads(l) for l in open(args.queries) if l.strip()]
    gold = {g["id"]: g for g in (json.loads(l) for l in open(args.gold) if l.strip())}
    n = matched = cited = placeholder = 0
    for q in queries:
        qid = q.get("id"); question = q.get("question") or q.get("query"); qt = q.get("query_time", "")
        ans = drive(args.base, question, qt)
        g = gold.get(qid, {})
        m = answer_matches(ans, g); cites = CITE.findall(ans)
        ph = any(c.startswith("doc-id::") for c in cites)
        n += 1; matched += m; cited += bool(cites); placeholder += ph
        print(f"[{qid}] match={'Y' if m else 'N'} cites={cites or '-'} :: {ans[:90]}")
    print(f"\n== ERROR-GATE (n={n}) ==")
    print(f"answer-match:       {matched}/{n} = {matched/n:.0%}")
    print(f"emitted a citation: {cited}/{n} = {cited/n:.0%}")
    print(f"placeholder cite:   {placeholder}/{n}  (literal 'doc-id::' = grounding failure)")


if __name__ == "__main__":
    main()
