import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from teacher_certification.contracts import FrameworkVersion, ObservationEvidence

entity = FrameworkVersion("E-DEMO", "国际中文教师三维认证", 1)
record = ObservationEvidence("R-DEMO", entity.entity_id, "已登记")
print(json.dumps({"entity": entity.display_name, "revision": entity.revision, "record_state": record.category}, ensure_ascii=False))
