set -e
git init -q
commit() { echo "$1" >> history.txt; git add history.txt; git commit -q -m "$1" ${2:+-m "$2"}; }
commit "chore: initial commit"
git tag v2.3.1
commit "fix: retry failed uploads"
commit "feat: load settings from config.yml" "BREAKING CHANGE: settings.ini is no longer read; move your settings to config.yml."
commit "chore: format with ruff"
commit "perf: cache parsed templates"
