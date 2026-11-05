// MIT License
import Foundation

enum HelperError: Error, CustomStringConvertible {
    case message(String)
    var description: String { switch self { case .message(let value): return value } }
}

func argument(_ name: String) throws -> String {
    let args = CommandLine.arguments
    guard let index = args.firstIndex(of: name), index + 1 < args.count else {
        throw HelperError.message("Missing \(name)")
    }
    return args[index + 1]
}

func fail(_ error: Error) -> Never {
    FileHandle.standardError.write(Data(("trash-app: \(error)\n").utf8))
    exit(2)
}

do {
    let originalSource = URL(fileURLWithPath: try argument("--source"), isDirectory: true)
    let sourceValues = try originalSource.resourceValues(forKeys: [.isSymbolicLinkKey])
    guard sourceValues.isSymbolicLink != true else { throw HelperError.message("source bundle is a symlink") }
    let source = originalSource.resolvingSymlinksInPath().standardizedFileURL
    let receipt = URL(fileURLWithPath: try argument("--receipt"))
    let expected = try argument("--expected-id")
    guard expected == "no.oddmund.storyboard.raven" else { throw HelperError.message("unsupported application family") }
    let canonicalArgument = CommandLine.arguments.contains("--canonical") ? try argument("--canonical") : ""
    let canonicalURLs = [FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications/Sprintwake Studios.app").resolvingSymlinksInPath().standardizedFileURL] + (canonicalArgument.isEmpty ? [] : [URL(fileURLWithPath: canonicalArgument, isDirectory: true).resolvingSymlinksInPath().standardizedFileURL])
    let fm = FileManager.default
    guard !canonicalURLs.contains(where: { source == $0 || $0.path.hasPrefix(source.path + "/") || source.path.hasPrefix($0.path + "/") }) else { throw HelperError.message("canonical application is protected") }
    guard fm.fileExists(atPath: source.path) else { throw HelperError.message("source is not an app bundle") }
    let contents = source.appendingPathComponent("Contents")
    let info = contents.appendingPathComponent("Info.plist")
    for url in [contents, info] {
        let attrs = try url.resourceValues(forKeys: [.isSymbolicLinkKey])
        guard attrs.isSymbolicLink != true else { throw HelperError.message("bundle contains a symlinked root") }
    }
    guard let plist = NSDictionary(contentsOf: info), let identifier = plist["CFBundleIdentifier"] as? String else { throw HelperError.message("missing bundle identifier") }
    let valid = identifier == expected || (identifier.hasPrefix(expected + ".qa.") && identifier.count == expected.count + 4 + 32 && identifier.dropFirst(expected.count + 4).allSatisfy { $0.isHexDigit })
    guard valid else { throw HelperError.message("unexpected bundle identifier") }
    var resulting: NSURL?
    try fm.trashItem(at: source, resultingItemURL: &resulting)
    guard let trashURL = resulting as URL?, trashURL.path.hasPrefix(fm.homeDirectoryForCurrentUser.appendingPathComponent(".Trash").path + "/"), fm.fileExists(atPath: trashURL.path) else { throw HelperError.message("invalid resulting Trash URL") }
    let output: [String: Any] = ["source": source.path, "trash": trashURL.path, "bundleIdentifier": identifier]
    let data = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: receipt, options: .atomic)
    let stdoutData = try JSONSerialization.data(withJSONObject: ["source": source.path, "trash": trashURL.path], options: [])
    FileHandle.standardOutput.write(stdoutData)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch { fail(error) }
