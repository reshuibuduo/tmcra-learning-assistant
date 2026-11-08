import Foundation

struct HintCard: Equatable {
    var kind = "concept"
    var title = ""
    var explanation = ""
    var example = ""
    var quote = ""
    var isEmpty: Bool { title.isEmpty || explanation.isEmpty }
}

struct PanelState: Equatable {
    var connected = false
    var demo = false
    var captureBusy = false
    var canStart = false
    var canStop = false
    var status = "尚未连接学习服务"
    var duration = "00:00"
    var title = "新的课堂"
    var card = HintCard()

    init(_ message: [String: Any] = [:]) {
        func string(_ key: String, _ limit: Int, _ fallback: String = "") -> String {
            guard let value = message[key] as? String else { return fallback }
            return String(value.prefix(limit))
        }
        connected = message["connected"] as? Bool ?? false
        demo = message["demo"] as? Bool ?? false
        captureBusy = message["captureBusy"] as? Bool ?? false
        canStart = connected && !captureBusy && (message["canStart"] as? Bool ?? false)
        canStop = captureBusy && (message["canStop"] as? Bool ?? false)
        status = string("status", 180, "尚未连接学习服务")
        duration = string("duration", 12, "00:00")
        title = string("title", 120, "新的课堂")
        if let raw = message["card"] as? [String: Any] {
            card = HintCard(kind: raw["kind"] as? String == "learning_memory" ? "learning_memory" : "concept",title: String((raw["title"] as? String ?? "").prefix(80)),
                            explanation: String((raw["explanation"] as? String ?? "").prefix(450)),
                            example: String((raw["example"] as? String ?? "").prefix(250)),
                            quote: String((raw["quote"] as? String ?? "").prefix(200)))
        }
    }
}

enum WorkbenchAddress {
    static let defaultURL = URL(string: "http://127.0.0.1:18782/")!
    static func validate(_ raw: String) -> URL? {
        guard let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              ["127.0.0.1", "localhost", "[::1]", "::1"].contains(url.host?.lowercased() ?? ""),
              url.user == nil, url.password == nil, (url.path.isEmpty || url.path == "/"),
              url.query == nil else { return nil }
        return url
    }
    static func sameOrigin(_ lhs: URL?, _ rhs: URL?) -> Bool {
        guard let lhs, let rhs else { return false }
        func port(_ u: URL) -> Int { u.port ?? (u.scheme == "https" ? 443 : 80) }
        return lhs.scheme == rhs.scheme && lhs.host == rhs.host && port(lhs) == port(rhs)
    }
    static func withoutSecret(_ url: URL) -> URL {
        var c = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        c.fragment = nil
        return c.url!
    }
    static func bundledPage(_ url: URL?, resources: URL?) -> Bool {
        guard let url, let resources, url.isFileURL,
              ["offline.html","demo.html"].contains(url.lastPathComponent) else {return false}
        return url.deletingLastPathComponent().standardizedFileURL.path == resources.standardizedFileURL.path
    }
}
