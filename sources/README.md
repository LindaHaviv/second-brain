# sources/ — your canonical content layer

This is where file-first collected content lands as Markdown + YAML frontmatter — one file
per post/video — created by the file-writing collectors in `../scripts/` (e.g.
`scripts/youtube.py` writes `sources/youtube/<id>.md`). API loaders (Notion, Drive, socials,
chat exports) write to the database directly and can re-pull anytime.

For content here, this is the **source of truth**: the Oracle database is a derived,
rebuildable view of it.

Per-platform content here is **gitignored** (`sources/*/`) so you don't publish your own
content — generate yours by following the tutorial (`../docs/TUTORIAL.md`).
