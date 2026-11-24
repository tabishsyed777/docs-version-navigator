"""Version-scoped, offline retrieval and extractive answer generation."""
import argparse
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata

MODEL = "word-count-cosine-v1"
FORMAT = "1"
FILE_LIMIT = 512 * 1024
UNKNOWN = "I don't know."
STOP = set("a an the what which how do does i we is are for of in with to as on use using me show tell please about give my can enabled default by value setting syntax configure compare summarize list quote exactly and or it need much many also set explain between tho whats s".split())
SENSITIVE = re.compile(r"[\w.+-]{1,64}@[\w.-]{1,253}\.[A-Za-z]{2,}|(?:api[_ -]?key|password|token)\s*[:=]\s*\S+|(?:\+?\d[\d ().-]{8,30}\d|/home/|/Users/|sk-[A-Za-z0-9]{12,})", re.I)
INJECTION = re.compile(r"ignore (?:all |the )?(?:previous |prior )?instructions|reveal (?:the )?system prompt", re.I)
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def clean(text):
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def search_terms(text):
    return [w for w in re.findall(r"[^\W_]+", text.casefold()) if w not in STOP]


def chunks(text, size=800):
    """Soft character target. Keep paragraphs, fenced blocks and table rows together."""
    if size < 1:
        raise ValueError("chunk size must be positive")
    result = []
    start = pos = 0
    fenced = False
    for line in text.splitlines(keepends=True):
        pos += len(line)
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and not line.strip() and pos - start >= size:
            result.append((start, pos, text[start:pos]))
            start = pos
    if text[start:].strip():
        result.append((start, len(text), text[start:]))
    return result


@dataclass(frozen=True)
class Passage:
    id: str
    source: str
    version: str
    title: str
    published: str
    start: int
    end: int
    text: str
    aliases: str
    see: str = ""


def load(path):
    if path.suffix != ".json" or path.is_symlink():
        raise ValueError("expected a regular JSON document")
    if not path.is_file():
        raise ValueError("missing document")
    with path.open("rb") as stream:
        raw = stream.read(FILE_LIMIT + 1)
    if len(raw) > FILE_LIMIT:
        raise ValueError("document exceeds size limit")
    doc = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(doc, dict):
        raise ValueError("document must be an object")
    for key in ("version", "source"):
        if not isinstance(doc.get(key), str) or not SAFE_ID.fullmatch(doc[key]):
            raise ValueError("invalid document " + key)
    if not isinstance(doc.get("date"), str):
        raise ValueError("missing publication date")
    date.fromisoformat(doc["date"])
    if not isinstance(doc.get("sections"), list) or not doc["sections"]:
        raise ValueError("expected sections")
    records = []
    for section in doc["sections"]:
        if not isinstance(section, dict):
            raise ValueError("invalid section")
        for key in ("title", "text", "aliases"):
            if not isinstance(section.get(key), str) or not clean(section[key]):
                raise ValueError("missing section " + key)
        title, body, aliases = (clean(section[k]) for k in ("title", "text", "aliases"))
        if SENSITIVE.search(" ".join((title, body, aliases, doc["source"]))):
            raise ValueError("sensitive data detected")
        if INJECTION.search(body):
            raise ValueError("instruction-like content detected")
        related = section.get("see", "")
        if not isinstance(related, str):
            raise ValueError("invalid related section")
        for start, end, part in chunks(body):
            identity = [doc["version"], doc["source"], title, start, part]
            digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
            records.append(Passage(digest, doc["source"], doc["version"], title,
                                   doc["date"], start, end, part, aliases, related))
    return records


def fingerprint(root):
    """Read a bounded amount per file. Refuse stale or altered snapshots."""
    digest = hashlib.sha256()
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ValueError("unexpected corpus entry")
        with path.open("rb") as stream:
            data = stream.read(FILE_LIMIT + 1)
        if len(data) > FILE_LIMIT:
            raise ValueError("document exceeds size limit")
        digest.update(json.dumps(path.name).encode() + hashlib.sha256(data).digest())
    return digest.hexdigest()


def sync(root, db):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("corpus directory missing")
    with closing(sqlite3.connect(db, timeout=5)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS passages(id TEXT PRIMARY KEY, version TEXT, record TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS config(format TEXT, model TEXT, root TEXT, digest TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS integrity(version TEXT PRIMARY KEY, digest TEXT)")
        before = fingerprint(root)
        conn.execute("DELETE FROM passages")
        conn.execute("DELETE FROM config")
        conn.execute("INSERT INTO config VALUES(?,?,?,?)", (FORMAT, MODEL, str(root.resolve()), before))
        for path in sorted(root.iterdir()):
            for item in load(path):
                conn.execute("INSERT OR IGNORE INTO passages VALUES(?,?,?)",
                             (item.id, item.version, json.dumps(asdict(item))))
        conn.execute("DELETE FROM integrity")
        for (version,) in conn.execute("SELECT DISTINCT version FROM passages"):
            records = stored_records(conn, version)
            conn.execute("INSERT INTO integrity VALUES(?,?)", (version, record_digest(records)))
        if fingerprint(root) != before:
            raise ValueError("corpus changed during sync; retry")


def stored_records(conn, version):
    return conn.execute("SELECT id,record FROM passages WHERE version=? ORDER BY id", (version,)).fetchall()


def record_digest(records):
    digest = hashlib.sha256()
    for record in records:
        digest.update(json.dumps(record).encode())
    return digest.hexdigest()


def read_versions(db, versions):
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn, conn:
        conn.execute("BEGIN")
        rows = conn.execute("SELECT format,model,root,digest FROM config").fetchall()
        if len(rows) != 1 or rows[0][:2] != (FORMAT, MODEL):
            raise ValueError("index incompatible; run sync")
        if fingerprint(Path(rows[0][2])) != rows[0][3]:
            raise ValueError("stale index; run sync")
        output = {}
        for version in versions:
            records = stored_records(conn, version)
            expected = conn.execute("SELECT digest FROM integrity WHERE version=?", (version,)).fetchone()
            if (records or expected) and expected != (record_digest(records),):
                raise ValueError("index records inconsistent; run sync")
            output[version] = [Passage(**json.loads(record)) for _, record in records]
        return output


def vector(text):
    counts = Counter(search_terms(text))
    norm = math.sqrt(sum(n*n for n in counts.values()))
    return {key: n/norm for key, n in counts.items()}


def rank(query, passages, k=5):
    if k < 0:
        raise ValueError("k must be non-negative")
    if not passages or k == 0:
        return []
    terms = set(search_terms(query))
    if not terms:
        return []
    qvec = vector(query)
    texts = [p.title + " " + p.aliases + " " + p.text for p in passages]
    bags = [Counter(search_terms(t)) for t in texts]
    df = Counter(term for bag in bags for term in bag)
    avg = sum(map(lambda bag: sum(bag.values()), bags)) / len(bags) or 1
    hits = []
    for p, text, bag in zip(passages, texts, bags):
        overlap = terms.intersection(bag)
        if not overlap:
            continue
        length = sum(bag.values())
        bm25 = sum(math.log1p((len(bags)-df[w]+0.5)/(df[w]+0.5)) *
                   bag[w]*2.2/(bag[w]+1.2*(0.25+0.75*length/avg)) for w in overlap)
        dv = vector(text)
        cosine = sum(qvec[w]*dv[w] for w in overlap)
        score = 0.7 * (bm25/(1+bm25)) + 0.3*cosine
        if terms == set(search_terms(p.title)):
            score += 1.0
        hits.append((score, p))
    return sorted(hits, key=lambda h: (-h[0], h[1].id))[:k]


SCOPE = re.compile(
    r"(?<![\w.])(?:(?:for|in|on|with)\s+)?(?:version\s+|release\s+|v(?=\d))"
    r"(?P<named>\d+(?:\.\d+)+(?:[-+][\w.-]+)?)\b|"
    r"\b(?:in|on|for)\s+(?P<bare>\d+(?:\.\d+)+)\b(?!\s*(?:ms|seconds?|MiB|MB|units?)\b)", re.I)


def strip_scope(query, versions):
    def remove(match):
        value = match.group("named") or match.group("bare")
        if value not in versions:
            raise ValueError("version conflict between the question and --version")
        return " "
    # Version-shaped literals in examples do not select a manual.
    query=re.sub(r"""(`+)([\s\S]*?)\1|"[^"]*"|'[^']*'""",
                 lambda m: " " if re.search(r"\d+\.\d+",m.group()) else m.group(),query)
    query=re.sub(r"\b(?:example|e\.g\.|returns|equals)\s*[:=]?\s*(?:in\s+)?(?:(?:version|release)\s+|v)?\d+(?:\.\d+)+"," ",query,flags=re.I)
    return SCOPE.sub(remove, query)


def answer(query, versions, db, k=5):
    if not query.strip() or len(query) > 2048 or not 1 <= k <= 100:
        raise ValueError("provide 1–2048 query characters and k within 1–100")
    versions = list(dict.fromkeys(versions))
    if not 1 <= len(versions) <= 4 or any(not SAFE_ID.fullmatch(v) for v in versions):
        raise ValueError("provide one to four version identifiers")
    query = strip_scope(query, versions)
    evidence, rankings = [], []
    status = "answered"
    for version, passages in read_versions(db, versions).items():
        vocabulary = sorted({w for p in passages for w in search_terms(p.title+" "+p.aliases+" "+p.text)})
        for clause in re.split(r"\s+and\s+|\s*;\s*", query, flags=re.I):
            clause=re.sub(r"\b(?:could you|would you|kindly|clarify|describe|according to the manual)\b"," ",clause,flags=re.I)
            for phrase,target in [("extra tries","retries"),("give up waiting","timeout"),("largest upload","upload limit")]:
                if all(w in vocabulary for w in search_terms(target)):
                    clause=re.sub(r"\b"+phrase+r"\b",target,clause,flags=re.I)
            words = search_terms(clause)
            normalized = []
            for word in words:
                match = difflib.get_close_matches(word, vocabulary, n=1, cutoff=0.84) if word not in vocabulary and len(word) >= 5 else []
                normalized.append(match[0] if match else word)
            clause = " ".join(normalized)
            hits = rank(clause, passages, k)
            rankings.append([p.id for _, p in hits])
            terms = set(search_terms(clause))
            candidates = [p for _, p in hits if terms and terms <= set(search_terms(p.title+" "+p.aliases+" "+p.text))]
            exact_title = any(terms == set(search_terms(p.title)) for p in candidates)
            matching_topics = {p.title for p in passages if terms and
                               terms <= set(search_terms(p.title + " " + p.aliases))}
            if terms and terms <= {"maximum", "limit", "minimum"}:
                matching_topics = {p.title for p in passages if
                                   terms.intersection(search_terms(p.title + " " + p.aliases))}
            if not exact_title and len(matching_topics) > 1:
                status = "ambiguous"
                continue
            if INJECTION.search(query) or not candidates:
                status = "insufficient_evidence"
                continue
            chosen = candidates[0]
            # Inspect all matching sections, including conflicts outside top-k.
            same = [p for p in passages if p.title.casefold() == chosen.title.casefold()]
            if len({p.source for p in same}) > 1 or len({p.start for p in same}) != len(same):
                status = "ambiguous"
                continue
            evidence.extend(sorted(same, key=lambda p: p.start))
            if chosen.see:
                linked = [p for p in passages if p.title == chosen.see]
                if len(linked) != 1:
                    status = "insufficient_evidence"
                    continue
                evidence.extend(linked)
                rankings.append([linked[0].id])
    cited = {p.id: p for p in evidence} if status == "answered" else {}
    return {"status": status, "answer": "\n".join(
        f"[{p.version} · {p.source} · {p.title}] {p.text}" for p in cited.values()) or UNKNOWN,
        "citations": [{key: value for key, value in asdict(p).items() if key not in ("aliases", "see")} for p in cited.values()],
        "retrieved": list(dict.fromkeys(ids[i] for i in range(k) for ids in rankings if i < len(ids))), "model": MODEL}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cmd = cli.add_subparsers(dest="command", required=True)
    build = cmd.add_parser("sync", help="atomically replace snapshot from a directory of JSON manuals")
    build.add_argument("corpus", type=Path)
    ask = cmd.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--version", action="append", required=True)
    ask.add_argument("--top-k", type=int, default=5)
    for parser in (build, ask):
        parser.add_argument("--db", type=Path, default=Path("navigator.sqlite"))
    args = cli.parse_args()
    try:
        if args.command == "sync":
            sync(args.corpus, args.db)
        else:
            print(json.dumps(answer(args.question, args.version, args.db, args.top_k), ensure_ascii=False, indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Navigator error ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
