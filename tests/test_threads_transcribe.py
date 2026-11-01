"""Pure parts of the Threads loader and the transcript step (no DB, no network)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "oracle" / "agent"))
import threads_apify as th   # noqa: E402
import transcribe_posts as tp  # noqa: E402


def test_threads_parse_keeps_only_own_top_level_posts():
    rows = [
        {"username": "lindavivah", "post_url": "https://www.threads.com/@lindavivah/post/A1",
         "text_content": "AI infra in 90 seconds\nthe stack", "created_at_timestamp": 1760000000,
         "like_count": 12, "reply_count": 3, "view_count": 900, "media_type": "video",
         "media_url": "https://cdn/x.mp4", "record_type": "post"},
        {"username": "someoneelse", "post_url": "https://www.threads.com/@someoneelse/post/B2",
         "text_content": "not hers", "record_type": "post"},
        {"username": "lindavivah", "post_url": "https://www.threads.com/@lindavivah/post/C3",
         "text_content": "a reply", "is_reply": True, "record_type": "post"},
        {"username": "lindavivah", "post_url": "https://www.threads.com/@lindavivah/post/D4",
         "text_content": "a repost", "is_repost": True, "record_type": "post"},
    ]
    out = th.parse_items(rows, "lindavivah")
    assert len(out) == 1
    p = out[0]
    assert p["kind"] == "video" and p["media_url"] == "https://cdn/x.mp4"
    assert p["title"] == "AI infra in 90 seconds" and p["likes"] == 12 and p["views"] == 900
    assert p["published_at"].year == 2025


def test_chunking_and_markers():
    text = " ".join(["word"] * 1000)
    ch = tp.chunks_of(text, size=1500)
    assert all(len(c) <= 1500 for c in ch) and sum(len(c.split()) for c in ch) == 1000
    assert tp.TRANSCRIPT_SEQ == 1000   # the "has a transcript" marker; caption chunks stay below


if __name__ == "__main__":
    for fn in (test_threads_parse_keeps_only_own_top_level_posts, test_chunking_and_markers):
        fn(); print(f"ok  {fn.__name__}")
    print("2/2 passed")
