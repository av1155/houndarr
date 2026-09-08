# assets

Generated files only. This branch is orphaned from `main` and carries no
source code.

`star-history-light.svg` and `star-history-dark.svg` are rebuilt daily by the
`Star History` workflow on `main` and embedded in the project README. They live
on a separate branch because `main` requires pull requests, and a daily
chart refresh is not worth a daily PR.

Do not edit these by hand; the next scheduled run overwrites them. To change
how the chart looks, edit `scripts/star_history.py` on `main`.
