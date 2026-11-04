import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

func readEnv(_ path: String) -> [String: String] {
  guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return [:] }
  var env: [String: String] = [:]
  for line in text.split(separator: "\n") {
    let trimmed = line.trimmingCharacters(in: .whitespaces)
    guard !trimmed.hasPrefix("#"), let eq = trimmed.firstIndex(of: "=") else { continue }
    let key = String(trimmed[..<eq]).trimmingCharacters(in: .whitespaces)
    var value = String(trimmed[trimmed.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
    if value.count >= 2, let first = value.first, first == value.last, first == "\"" || first == "'" {
      value = String(value.dropFirst().dropLast())
    }
    env[key] = value
  }
  return env
}

let env = readEnv(".env")
var secrets: [String] = []
func add(_ value: String?) {
  let text = (value ?? "").trimmingCharacters(in: .whitespaces).lowercased()
  if text.count >= 3 { secrets.append(text) }
}
add(env["BOT_ACCOUNT_NAME"])
add(env["BOT_ACCOUNT_EMAIL"])
add(env["BOT_ACCOUNT_EMAIL"]?.split(separator: "@").first.map(String.init))
add(env["HOST_NAME"])
for list in [env["BOT_ACCOUNT_OTHER_NAMES"], env["GUEST_NAMES"]] {
  for name in (list ?? "").split(separator: ",") { add(String(name)) }
}
for name in [env["BOT_ACCOUNT_NAME"], env["HOST_NAME"]] {
  for word in (name ?? "").split(separator: " ") where word.count >= 4 { add(String(word)) }
}
if secrets.isEmpty {
  FileHandle.standardError.write("Nothing to look for: .env has no names or address.\n".data(using: .utf8)!)
  exit(1)
}

func distance(_ a: [Character], _ b: [Character]) -> Int {
  var row = Array(0...b.count)
  for i in 1...max(a.count, 1) where !a.isEmpty {
    var previous = row[0]
    row[0] = i
    for j in 1...max(b.count, 1) where !b.isEmpty {
      let current = row[j]
      row[j] = min(row[j] + 1, row[j - 1] + 1, previous + (a[i - 1] == b[j - 1] ? 0 : 1))
      previous = current
    }
  }
  return row[b.count]
}

func leaks(_ line: String) -> Bool {
  let lower = line.lowercased()
  if secrets.contains(where: { lower.contains($0) }) { return true }
  let words = lower.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map { Array($0) }
  return secrets.contains { secret in
    secret.count >= 5 && !secret.contains(" ") && words.contains { abs($0.count - secret.count) <= 1 && distance($0, Array(secret)) <= 1 }
  }
}

func pngs(_ path: String) -> [String] {
  var isDir: ObjCBool = false
  guard FileManager.default.fileExists(atPath: path, isDirectory: &isDir) else { return [] }
  if !isDir.boolValue { return path.hasSuffix(".png") ? [path] : [] }
  let found = FileManager.default.enumerator(atPath: path)?.compactMap { $0 as? String } ?? []
  return found.filter { $0.hasSuffix(".png") }.map { (path as NSString).appendingPathComponent($0) }.sorted()
}

let args = Array(CommandLine.arguments.dropFirst())
let check = args.contains("--check")
let targets = args.filter { $0 != "--check" }
let files = (targets.isEmpty ? ["../../captures/T0.2"] : targets).flatMap(pngs)
var total = 0
for file in files {
  let url = URL(fileURLWithPath: file)
  guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
    let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
  else { print("\(file): could not read"); continue }
  let request = VNRecognizeTextRequest()
  request.recognitionLevel = .accurate
  request.usesLanguageCorrection = false
  try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
  let boxes = (request.results ?? []).filter { leaks($0.topCandidates(1).first?.string ?? "") }.map(\.boundingBox)
  if boxes.isEmpty { continue }
  total += boxes.count
  print("\(file): \(boxes.count) line(s) \(check ? "found" : "painted over")")
  if check { continue }
  let width = CGFloat(image.width), height = CGFloat(image.height), margin: CGFloat = 6
  guard let context = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: 0,
    space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
  else { continue }
  context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
  context.setFillColor(CGColor(red: 1, green: 0, blue: 1, alpha: 1))
  for box in boxes {
    context.fill(CGRect(x: box.minX * width - margin, y: box.minY * height - margin,
      width: box.width * width + 2 * margin, height: box.height * height + 2 * margin))
  }
  guard let covered = context.makeImage(),
    let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
  else { continue }
  CGImageDestinationAddImage(destination, covered, nil)
  CGImageDestinationFinalize(destination)
}
print("\(files.count) screenshot(s) read, \(total) line(s) \(check ? "found" : "painted over").")
exit(check && total > 0 ? 2 : 0)
