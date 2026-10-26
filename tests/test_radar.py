from techradar.radar import RadarService, derive_status
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository


def test_radar_preserves_manual_decision_and_updates_suggestion(config, database):
    item = TechItem(source_type="github", source_name="test", title="Simulator", url="https://example.com/sim", metadata={"repo": "acme/sim"})
    ItemRepository(database).insert(item)
    AnalysisRepository(database).upsert_analysis(item.id, {"score": 95, "llm_status": "llm", "recommended_action": "POC"})
    conn = database.connect()
    try:
        with conn:
            conn.execute("INSERT INTO radar(tech_name,status,decision_status,decision_reason,reason,action,owner) VALUES('acme/sim','ARCHIVE','ARCHIVE','Not compatible','Human evidence','Do not adopt','Alice')")
    finally:
        conn.close()
    RadarService(config, database).sync()
    row = RadarService(config, database).list_all()[0]
    assert row["status"] == row["decision_status"] == "ARCHIVE"
    assert row["suggested_status"] == "POC"
    assert (row["reason"], row["action"], row["owner"]) == ("Human evidence", "Do not adopt", "Alice")


def test_radar_legacy_adopt_is_preserved(config, database):
    item = TechItem(source_type="arxiv", source_name="test", title="Technique", url="https://example.com/tech")
    ItemRepository(database).insert(item)
    AnalysisRepository(database).upsert_analysis(item.id, {"score": 80})
    conn = database.connect()
    try:
        with conn:
            conn.execute("INSERT INTO radar(tech_name,status,reason) VALUES('Technique','ADOPT','Human decision')")
    finally:
        conn.close()
    RadarService(config, database).sync()
    assert RadarService(config, database).list_all()[0]["status"] == "ADOPT"


def test_radar_thresholds_follow_config():
    assert derive_status(70, {"focus": 65, "strong_focus": 80}) == "INVESTIGATE"
    assert derive_status(70, {"focus": 60, "poc": 70}) == "POC"
    assert derive_status(None) == "WATCH"
