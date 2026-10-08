"""Gateway PATCH serialization (agdemo_core/gcp/engines.py)."""
from agdemo_core.config import DemoConfig
from agdemo_core.gcp import engines
from agdemo_core.gcp.rest import GcpError

ENGINE = "projects/1/locations/us-east4/reasoningEngines/9"
EG = "projects/p/locations/us-east4/agentGateways/eg"
IG = "projects/p/locations/us-east4/agentGateways/ig"


class FakeRest:
    def __init__(self, cfg, busy_times=0):
        self.cfg, self.busy, self.patches = cfg, busy_times, []

    def get(self, url, ok404=False, params=None):
        if url.endswith("/operations"):
            return {"operations": []}
        return {"spec": {"deploymentSpec": {"agentGatewayConfig": self.cfg}}}

    def patch(self, url, json=None, params=None):
        if self.busy:
            self.busy -= 1
            raise GcpError(400, "Reasoning Engine '9' is already being bound or unbound to Agent Gateway")
        self.patches.append(json["spec"]["deploymentSpec"]["agentGatewayConfig"])
        self.cfg = self.patches[-1]
        return {"name": "op/1"}


def test_detach_both_in_one_patch_and_retry_when_busy(monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "_persist", lambda *a: None)
    monkeypatch.setattr(engines.time, "sleep", lambda s: None)
    cfg = DemoConfig.model_validate({"environment": {"project_id": "p"}})
    r = FakeRest({"agentToAnywhereConfig": {"agentGateway": EG}, "clientToAgentConfig": {"agentGateway": IG}},
                 busy_times=2)
    out = engines.set_gateway_paths(r, cfg, ENGINE, {"egress": None, "ingress": None})
    assert out["changed"] and r.patches == [{}]


def test_one_direction_preserves_the_other(monkeypatch):
    monkeypatch.setattr(engines, "_persist", lambda *a: None)
    cfg = DemoConfig.model_validate({"environment": {"project_id": "p"}})
    r = FakeRest({"agentToAnywhereConfig": {"agentGateway": EG}, "clientToAgentConfig": {"agentGateway": IG}})
    engines.set_gateways(r, cfg, ENGINE, "ingress", None)
    assert r.patches == [{"agentToAnywhereConfig": {"agentGateway": EG}}]
    assert engines.set_gateways(r, cfg, ENGINE, "ingress", None) == {"changed": False}
