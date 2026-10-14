function parseLine(line, sep = ',') {
  return line.split(sep);
}

module.exports = { parseLine };
