set -e
git init -q
git config user.name "Release Bot"
git config user.email "bot@example.com"
export GIT_AUTHOR_DATE="2026-08-01T10:00:00Z" GIT_COMMITTER_DATE="2026-08-01T10:00:00Z"
git add package.json src/parse.js README.md
git commit -q -m "feat: initial parser" --author="Priya Raman <priya@example.com>"
git tag v1.4.1
export GIT_AUTHOR_DATE="2026-08-20T10:00:00Z" GIT_COMMITTER_DATE="2026-08-20T10:00:00Z"
echo "// tabs supported" >> src/parse.js
git add src/parse.js
git commit -q -m "fix: support tab separator" --author="Priya Raman <priya@example.com>"
git tag v1.4.2
export GIT_AUTHOR_DATE="2026-09-02T10:00:00Z" GIT_COMMITTER_DATE="2026-09-02T10:00:00Z"
printf 'function parseLine(line, sep = \x27,\x27) {\n  return line.replace(/\\r$/, \x27\x27).split(sep);\n}\n\nmodule.exports = { parseLine };\n' > src/parse.js
git add src/parse.js
git commit -q -m "fix: strip trailing carriage return from lines" -m "Windows-style files left a stray \\r on the last field. No breaking changes to the API." --author="Marcus Webb <marcus@example.com>"
export GIT_AUTHOR_DATE="2026-09-10T10:00:00Z" GIT_COMMITTER_DATE="2026-09-10T10:00:00Z"
printf '# csvkit-lite\n\nA tiny CSV parser.\n\n## Usage\n\n    parseLine(\x27a,b,c\x27)\n' > README.md
git add README.md
git commit -q -m "docs: add usage example to README" -m "Mentions the new feature request template too." --author="Lena Ortiz <lena@example.com>"
export GIT_AUTHOR_DATE="2026-09-18T10:00:00Z" GIT_COMMITTER_DATE="2026-09-18T10:00:00Z"
echo "node_modules/" > .gitignore
git add .gitignore
git commit -q -m "chore: add gitignore" --author="Release Bot <bot@example.com>"
export GIT_AUTHOR_DATE="2026-09-25T10:00:00Z" GIT_COMMITTER_DATE="2026-09-25T10:00:00Z"
echo "// handle empty input" >> src/parse.js
git add src/parse.js
git commit -q -m "fix: return empty array for empty line" --author="Priya Raman <priya@example.com>"
