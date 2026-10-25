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

private final class ExactEventResponseProtocol: URLProtocol {
    static var responseStatus = 400
    static var responseBody = Data()

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: Self.responseStatus,
            httpVersion: nil,
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.responseBody)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

final class ExactEventFeedbackTests: XCTestCase {
    @MainActor
    func testServerRejectionKeepsAddResultFalseAndExposesMessage() async {
        ExactEventResponseProtocol.responseStatus = 400
        ExactEventResponseProtocol.responseBody = Data(#"{"error":"Source was rejected."}"#.utf8)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ExactEventResponseProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let store = ChusennoteStore(session: session)

        let added = await store.addExactEvent(
            keyword: "YOASOBI",
            title: "Tour page",
            url: "https://example.com/tour",
            snippet: "Ticket window"
        )

        XCTAssertFalse(added)
        XCTAssertEqual(store.errorMessage, "Source was rejected.")
    }

    @MainActor
    func testSuccessfulHTTPWithoutAddedConfirmationIsNotReportedAsSuccess() async {
        ExactEventResponseProtocol.responseStatus = 200
        ExactEventResponseProtocol.responseBody = Data(#"{"added":false}"#.utf8)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ExactEventResponseProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let store = ChusennoteStore(session: session)

        let added = await store.addExactEvent(
            keyword: "YOASOBI",
            title: "Tour page",
            url: "https://example.com/tour",
            snippet: "Ticket window"
        )

        XCTAssertFalse(added)
        XCTAssertEqual(store.errorMessage, "The server did not add this event page.")
    }
}

/// Answers every request with an empty success and records the paths asked for.
private final class RecordingProtocol: URLProtocol {
    private static let lock = NSLock()
    private static var recorded: [String] = []

    static var paths: [String] {
        lock.lock()
        defer { lock.unlock() }
        return recorded
    }

    static func reset() {
        lock.lock()
        recorded = []
        lock.unlock()
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let path = request.url?.path ?? ""
        Self.lock.lock()
        Self.recorded.append(path)
        Self.lock.unlock()
        let body = path.hasSuffix("/remove") ? #"{"removed":true}"# : "[]"
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

final class TargetedRefreshTests: XCTestCase {
    @MainActor
    func testRemovingASubscriptionRefetchesOnlySubscriptions() async {
        RecordingProtocol.reset()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RecordingProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let store = ChusennoteStore(session: session)

        await store.removeSubscription(id: 7)

        XCTAssertNil(store.errorMessage)
        XCTAssertEqual(RecordingProtocol.paths, ["/api/subscriptions/remove", "/api/subscriptions"])
    }
}
