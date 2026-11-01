"""Transcribe the videos you actually published, across every platform, into the brain.

The loaders bring the REAL caption of every post (Apify self-scrapes for LinkedIn,
TikTok, X, Instagram, Threads; the API for YouTube). A video's spoken words are the
other half of what was really said, and until now only YouTube had them (captions
file) plus whatever you transcribed by hand. This step closes that: daily, it finds
published video posts with no transcript yet, pulls the audio with yt-dlp from the
post's public URL, transcribes it, and stores the transcript as content_chunks on the
post — the same shape youtube_transcripts.py writes, so search, the wiki and
/caption's Step 0 see it with no further changes.

Backends (TRANSCRIBE_BACKEND in oracle/.env; the sync step is skipped when unset):
  mlx     local mlx-whisper on the Mac (the privacy seat; nothing leaves the machine)
  openai  the Whisper API (what the hosted /diagram pipeline already uses for drafts);
          audio of PUBLIC posts leaves to OpenAI — fine for published content, and the
          only option on the cloud runner
Daily cap TRANSCRIBE_MAX (default 15) bounds time/cost; the backlog drains over days,
newest first. Failures are remembered (exports/transcribe_state.json) and a post is
retried at most 3 times, then listed in the log for a human.

Markers: transcript chunks use seq >= 1000 so they never collide with caption chunks
and "has a transcript" is one query. Idempotent per post.

  ./.venv/bin/python scripts/transcribe_posts.py            # the sync step
  ./.venv/bin/python scripts/transcribe_posts.py --dry-run  # list what would be done
"""
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
import db  # noqa: E402

STATE = ROOT / "exports" / "transcribe_state.json"
TRANSCRIPT_SEQ = 1000
VIDEO_PLATFORMS = ("instagram", "tiktok", "x", "youtube", "threads", "linkedin")
VIDEO_KINDS = ("reel", "video", "short", "clip", "episode")
MAX_PER_RUN = int(os.environ.get("TRANSCRIBE_MAX", "15"))
MAX_ATTEMPTS = 3


def chunks_of(text, size=1500):
    out, buf = [], ""
    for w in text.split():
        if buf and len(buf) + len(w) + 1 > size:
            out.append(buf)
            buf = w
        else:
            buf = f"{buf} {w}" if buf else w
    if buf:
        out.append(buf)
    return out


def _state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def candidates(cur, limit):
    """Published video posts (content scope) with no transcript chunks yet, newest first.
    A post counts as video by kind, or by having a video media row."""
    cur.execute(f"""
        select p.post_id, p.platform_id, p.url, p.kind
          from posts p
         where NVL(p.visibility,'content') = 'content'
           and p.platform_id in ({','.join(':p%d' % i for i in range(len(VIDEO_PLATFORMS)))})
           and (p.kind in ({','.join(':k%d' % i for i in range(len(VIDEO_KINDS)))})
                or exists (select 1 from media m where m.post_id = p.post_id and m.kind = 'video'))
           and p.url is not null
           and not exists (select 1 from content_chunks c
                            where c.post_id = p.post_id and c.seq >= :tseq)
         order by p.published_at desc nulls last
         fetch first :lim rows only""",
        **{f"p{i}": v for i, v in enumerate(VIDEO_PLATFORMS)},
        **{f"k{i}": v for i, v in enumerate(VIDEO_KINDS)},
        tseq=TRANSCRIPT_SEQ, lim=limit)
    return cur.fetchall()


def fetch_audio(url, workdir):
    """yt-dlp -> m4a in workdir. Works logged-out for public YouTube/TikTok/X/Instagram/
    Threads posts. Returns the path or raises RuntimeError with the tool's last line."""
    ytdlp = shutil.which("yt-dlp") or str(ROOT / ".venv" / "bin" / "yt-dlp")
    out = pathlib.Path(workdir) / "audio.%(ext)s"
    r = subprocess.run([ytdlp, "-q", "--no-warnings", "-x", "--audio-format", "m4a",
                        "--no-playlist", "-o", str(out), url],
                       capture_output=True, text=True, timeout=600)
    files = list(pathlib.Path(workdir).glob("audio.*"))
    if r.returncode != 0 or not files:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1:] or ["yt-dlp failed"])
    return files[0]


def transcribe(path, backend):
    if backend == "mlx":
        import mlx_whisper
        out = mlx_whisper.transcribe(str(path), path_or_hf_repo="mlx-community/whisper-large-v3-turbo")
        return (out.get("text") or "").strip()
    if backend == "openai":
        from openai import OpenAI
        with open(path, "rb") as f:
            r = OpenAI().audio.transcriptions.create(model="whisper-1", file=f)
        return (getattr(r, "text", None) or "").strip()
    raise SystemExit(f"TRANSCRIBE_BACKEND={backend!r}: use mlx (Mac) or openai (cloud)")


def store(cur, post_id, text):
    cur.execute("delete from content_chunks where post_id = :p and seq >= :s", p=post_id, s=TRANSCRIPT_SEQ)
    for i, ch in enumerate(chunks_of(text)):
        cur.execute("""insert into content_chunks (post_id, seq, chunk, embedding)
                       values (:p, :s, :c, vector_embedding(MINILM using :e as data))""",
                    p=post_id, s=TRANSCRIPT_SEQ + i, c=ch, e=ch[:3000])
    return i + 1


def main():
    dry = "--dry-run" in sys.argv
    backend = (os.environ.get("TRANSCRIBE_BACKEND") or "").lower()
    if not backend and not dry:
        sys.exit("transcribe_posts: set TRANSCRIBE_BACKEND=mlx|openai in oracle/.env")
    st = _state()
    conn = db.open_connection()
    cur = conn.cursor()
    rows = candidates(cur, MAX_PER_RUN * 2)
    todo = [r for r in rows if st.get(str(r[0]), {}).get("attempts", 0) < MAX_ATTEMPTS][:MAX_PER_RUN]
    skipped = [r for r in rows if r not in todo]
    print(f"transcribe: {len(todo)} to do this run, {len(skipped)} parked (max attempts or over cap)")
    if dry:
        for pid, plat, url, kind in todo:
            print(f"  would transcribe {plat}/{kind} post {pid}: {url}")
        return
    done = failed = 0
    for pid, plat, url, kind in todo:
        rec = st.setdefault(str(pid), {"attempts": 0})
        rec["attempts"] += 1
        rec["last"] = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            with tempfile.TemporaryDirectory() as td:
                audio = fetch_audio(url, td)
                text = transcribe(audio, backend)
            if len(text) < 40:
                raise RuntimeError("transcript empty or too short (no speech?)")
            n = store(cur, pid, text)
            conn.commit()
            rec["ok"] = True
            rec.pop("error", None)
            done += 1
            print(f"  ✓ {plat} {pid}: {len(text)} chars -> {n} chunks")
        except Exception as e:                  # noqa: BLE001 — one bad video never stops the run
            conn.rollback()
            rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            failed += 1
            print(f"  ✗ {plat} {pid}: {rec['error']}")
        _save_state(st)
    conn.close()
    parked = [k for k, v in st.items() if v.get("attempts", 0) >= MAX_ATTEMPTS and not v.get("ok")]
    print(f"transcribe: {done} done, {failed} failed this run; {len(parked)} parked after "
          f"{MAX_ATTEMPTS} attempts" + (f" (ids {', '.join(parked[:10])})" if parked else ""))
    sys.exit(1 if failed and not done else 0)


if __name__ == "__main__":
    main()
