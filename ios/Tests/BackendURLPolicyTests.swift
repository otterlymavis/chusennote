import Foundation
import XCTest
@testable import Chusennote

final class BackendURLPolicyTests: XCTestCase {
    func testDefaultBaseURLUsesProductionHTTPS() {
        XCTAssertEqual(ChusennoteSettings.defaultBaseURL, "https://chusennote.onrender.com")
        XCTAssertTrue(BackendURLPolicy.permitsCredentialTransport(ChusennoteSettings.defaultBaseURL))
    }

    func testExportComplianceDeclaresOnlyExemptEncryption() {
        XCTAssertEqual(
            Bundle.main.object(forInfoDictionaryKey: "ITSAppUsesNonExemptEncryption") as? Bool,
            false
        )
    }

    func testFirebaseMessagingMatchesBundledConfiguration() {
        let configuration = Bundle.main.url(forResource: "GoogleService-Info", withExtension: "plist")
        XCTAssertEqual(DeviceRegistration.firebaseMessagingConfigured, configuration != nil)
        if let configuration {
            let values = NSDictionary(contentsOf: configuration)
            XCTAssertEqual(values?["BUNDLE_ID"] as? String, Bundle.main.bundleIdentifier)
        }
    }

    func testCredentialTransportPolicy() {
        let accepted = [
            "https://api.example.com",
            "http://localhost:8877",
            "http://127.0.0.1:8877",
            "http://10.0.2.2:8877",
            "http://172.16.0.1",
            "http://172.31.255.255",
            "http://192.168.1.20:8877",
            "http://169.254.1.2",
            "http://[::1]:8877",
            "http://[fd00::1]:8877",
            "http://[fe80::1]:8877"
        ]
        let rejected = [
            "",
            "ftp://127.0.0.1",
            "http://example.com",
            "http://192.0.2.1",
            "http://172.32.0.1",
            "http://user:password@localhost:8877",
            "not a URL"
        ]

        for value in accepted where !BackendURLPolicy.permitsCredentialTransport(value) {
            XCTFail("Expected credential transport to be accepted: \(value)")
        }
        for value in rejected where BackendURLPolicy.permitsCredentialTransport(value) {
            XCTFail("Expected credential transport to be rejected: \(value)")
        }
    }

    func testCredentialBearingRedirectsAreRejected() {
        let delegate = RedirectRejectingSessionDelegate()
        let response = HTTPURLResponse(
            url: URL(string: "https://api.example.com/start")!,
            statusCode: 307,
            httpVersion: nil,
            headerFields: ["Location": "https://other.example.com/"]
        )!
        let request = URLRequest(url: URL(string: "https://other.example.com/")!)
        var redirectedRequest: URLRequest? = request
        delegate.urlSession(
            BackendSession.shared,
            task: BackendSession.shared.dataTask(with: URL(string: "https://api.example.com/start")!),
            willPerformHTTPRedirection: response,
            newRequest: request
        ) { redirectedRequest = $0 }
        XCTAssertNil(redirectedRequest, "Credential-bearing redirects must be rejected")
    }
}
