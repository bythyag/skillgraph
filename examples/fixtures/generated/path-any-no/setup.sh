set -e
git init -q
git config user.email "maya@example.com"
git config user.name "Maya Chen"
export GIT_AUTHOR_DATE="2026-08-01T10:00:00" GIT_COMMITTER_DATE="2026-08-01T10:00:00"
mv greeter.py .greeter_final.py
printf 'def greet(name):\n    return f"Hello, {name}!"\n' > greeter.py
git add README.md greeter.py
git commit -q -m "feat: initial greet function"
git tag v1.0.0
export GIT_AUTHOR_DATE="2026-09-01T10:00:00" GIT_COMMITTER_DATE="2026-09-01T10:00:00"
printf 'def greet(name):\n    name = name.strip() or "there"\n    return f"Hello, {name}!"\n' > greeter.py
git add greeter.py
git commit -q -m "fix: handle blank names" --author="Ravi Patel <ravi@example.com>"
git commit -q --allow-empty -m "chore: bump dev dependencies"
git tag v1.0.1
export GIT_AUTHOR_DATE="2026-10-01T10:00:00" GIT_COMMITTER_DATE="2026-10-01T10:00:00"
mv .greeter_final.py greeter.py
git add greeter.py
git commit -q -m "feat: add optional punctuation argument"
git tag v1.1.0
