import Foundation
import XCTest
@testable import Chusennote

final class EventModelTests: XCTestCase {
    func testHealthDecodesReleaseIdentityAndLegacyPayload() throws {
        let current = try JSONDecoder().decode(
            HealthSummary.self,
            from: Data(
                #"{"app":"chusennote","version":"0.1.0","build":1,"status":"ok","schema_version":16,"tracked_artists":2,"tracked_events":3,"saved_events":4,"manual_sources":5,"alerts":6}"#.utf8
            )
        )
        let legacy = try JSONDecoder().decode(
            HealthSummary.self,
            from: Data(
                #"{"app":"chusennote","status":"ok","schema_version":15,"tracked_artists":0,"tracked_events":0,"saved_events":0,"manual_sources":0,"alerts":0}"#.utf8
            )
        )

        XCTAssertEqual(current.version, "0.1.0")
        XCTAssertEqual(current.build, 1)
        XCTAssertEqual(current.releaseLabel, "v0.1.0 (1)")
        XCTAssertNil(legacy.version)
        XCTAssertNil(legacy.build)
        XCTAssertEqual(legacy.releaseLabel, "release unavailable")
    }

    func testEventDecodesOrganizerLineupAndOfficialResaleDates() throws {
        let payload = Data(
            #"""
            {
              "id": 17,
              "watch_id": 4,
              "title": "Runtime Stage",
              "status": "lottery_open",
              "status_label": "Ticket window open",
              "organizers": ["Example Productions"],
              "lineup": ["Example Lead", "Example Guest"],
              "related_events": [{
                "id": 18,
                "title": "Related Stage",
                "official_url": "https://official.example/related",
                "recommendation_reasons": ["Shared organizer: Example Productions"]
              }],
              "rounds": [{
                "name": "Official resale",
                "trade_start_at": "2026-07-01",
                "trade_end_at": "2026-07-03",
                "schedule_label": "Resale 2026-07-01 – 2026-07-03"
              }]
            }
            """#.utf8
        )

        let event = try JSONDecoder().decode(EventSummary.self, from: payload)

        XCTAssertEqual(event.organizers, ["Example Productions"])
        XCTAssertEqual(event.lineup, ["Example Lead", "Example Guest"])
        XCTAssertEqual(event.relatedEvents?.first?.title, "Related Stage")
        XCTAssertEqual(event.relatedEvents?.first?.recommendationReasons, ["Shared organizer: Example Productions"])
        XCTAssertEqual(eventStatusText(event), "Ticket window open")
        XCTAssertEqual(event.rounds.first?.tradeStartAt, "2026-07-01")
        XCTAssertEqual(event.rounds.first?.tradeEndAt, "2026-07-03")
    }

    func testLegacyEventPayloadStillDecodesWithoutAdditiveFields() throws {
        let payload = Data(#"{"id":18,"watch_id":5,"status":"lottery_open","rounds":[]}"#.utf8)

        let event = try JSONDecoder().decode(EventSummary.self, from: payload)

        XCTAssertNil(event.organizers)
        XCTAssertNil(event.lineup)
        XCTAssertNil(event.statusLabel)
        XCTAssertNil(event.relatedEvents)
        XCTAssertEqual(eventStatusText(event), "Ticket window open")
        XCTAssertTrue(event.rounds.isEmpty)
    }

    func testAlertTypeUsesServerLabelAndLegacyFallback() throws {
        let current = try JSONDecoder().decode(
            AlertPayload.self,
            from: Data(#"{"type":"lottery_closing_soon","type_label":"Lottery closing soon"}"#.utf8)
        )
        let legacy = try JSONDecoder().decode(
            AlertPayload.self,
            from: Data(#"{"type":"trade_opened"}"#.utf8)
        )

        XCTAssertEqual(alertTypeText(current), "Lottery closing soon")
        XCTAssertEqual(alertTypeText(legacy), "Official resale opened")
    }
}
