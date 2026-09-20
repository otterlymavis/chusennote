import Foundation
import Network

/// Transport rules for full-access API credentials and device-addressing push
/// tokens. Public servers require HTTPS; cleartext remains available only for
/// the local-first loopback/private-LAN workflow.
enum BackendURLPolicy {
    static let credentialTransportMessage =
        "Use HTTPS, localhost, or a literal private-network IP before signing in."

    static func permitsCredentialTransport(_ baseURL: String) -> Bool {
        guard let components = URLComponents(
            string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        ), let scheme = components.scheme?.lowercased(),
           let rawHost = components.host?.lowercased(),
           components.user == nil, components.password == nil else {
            return false
        }
        let host = rawHost.hasPrefix("[") && rawHost.hasSuffix("]")
            ? String(rawHost.dropFirst().dropLast())
            : rawHost
        if scheme == "https" {
            return true
        }
        guard scheme == "http" else { return false }
        if host == "localhost" {
            return true
        }
        if let address = IPv4Address(host) {
            let bytes = [UInt8](address.rawValue)
            return bytes[0] == 127
                || bytes[0] == 10
                || (bytes[0] == 172 && 16...31 ~= bytes[1])
                || (bytes[0] == 192 && bytes[1] == 168)
                || (bytes[0] == 169 && bytes[1] == 254)
        }
        if let address = IPv6Address(host) {
            let bytes = [UInt8](address.rawValue)
            let loopback = bytes.dropLast().allSatisfy { $0 == 0 } && bytes.last == 1
            let uniqueLocal = bytes[0] & 0xfe == 0xfc
            let linkLocal = bytes[0] == 0xfe && bytes[1] & 0xc0 == 0x80
            return loopback || uniqueLocal || linkLocal
        }
        return false
    }
}

/// URLSession follows redirects by default. Refusing them prevents bearer
/// credentials or push tokens from being forwarded away from the configured
/// backend host.
final class RedirectRejectingSessionDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

enum BackendSession {
    private static let delegate = RedirectRejectingSessionDelegate()
    static let shared = URLSession(configuration: .ephemeral, delegate: delegate, delegateQueue: nil)
}
