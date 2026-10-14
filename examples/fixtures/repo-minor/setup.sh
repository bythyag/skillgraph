set -e
git init -q
commit() { echo "$1" >> history.txt; git add history.txt; git commit -q -m "$1" ${2:+-m "$2"}; }
commit "chore: initial commit"
git tag v1.4.0
commit "feat: add CSV export"
commit "fix: handle empty input in the parser"
commit "chore: bump dev dependencies"
commit "docs: clarify install steps"
commit "fix: use the configured timezone in reports"
