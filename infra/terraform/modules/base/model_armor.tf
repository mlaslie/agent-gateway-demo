# bootstrap step 6: Model Armor template, the authz extension in each gateway project (no policy: the
# CONTENT_AUTHZ policies are the UI's Model Armor checkbox), and the callout roles for the service agents.

locals {
  ma_filters       = toset(var.model_armor_filters)
  ma_template_name = "projects/${var.project_id}/locations/${var.region}/templates/${var.names.model_armor_template}"
  # Same bytes as bootstrap's json.dumps([...]) (", " / ": " separators), so T2 imports with zero diff.
  ma_settings = "[{\"request_template_id\": \"${local.ma_template_name}\", \"response_template_id\": \"${local.ma_template_name}\"}]"
  ma_roles    = ["roles/modelarmor.calloutUser", "roles/modelarmor.user", "roles/serviceusage.serviceUsageConsumer"]

  # Static keys (values may be unknown until the gateways exist).
  ma_agents = merge(
    { for s in var.model_armor_static_agents : s => "serviceAccount:service-${var.project_number}@${s}.iam.gserviceaccount.com" },
    { for k, g in google_network_services_agent_gateway.gw : "gateway-${k}" => "serviceAccount:${g.agent_gateway_card[0].service_extensions_service_account}" },
  )
  # Static project service agents are shared with any other environment in the project (shared_project_grants).
  ma_agent_keys = concat(var.shared_project_grants ? var.model_armor_static_agents : [], ["gateway-egress", "gateway-ingress"])
  ma_grants = {
    for pair in setproduct(local.ma_agent_keys, local.ma_roles) :
    "${pair[0]} ${pair[1]}" => { agent = pair[0], role = pair[1] }
  }
}

resource "google_model_armor_template" "shield" {
  project     = var.project_id
  location    = var.region
  template_id = var.names.model_armor_template
  labels      = var.labels

  filter_config {
    dynamic "pi_and_jailbreak_filter_settings" {
      for_each = contains(local.ma_filters, "prompt_injection_jailbreak") ? [1] : []
      content {
        filter_enforcement = "ENABLED"
        confidence_level   = "MEDIUM_AND_ABOVE"
      }
    }
    dynamic "sdp_settings" {
      for_each = contains(local.ma_filters, "sensitive_data") ? [1] : []
      content {
        basic_config {
          filter_enforcement = "ENABLED"
        }
      }
    }
    dynamic "malicious_uri_filter_settings" {
      for_each = contains(local.ma_filters, "malicious_uri") ? [1] : []
      content {
        filter_enforcement = "ENABLED"
      }
    }
    dynamic "rai_settings" {
      for_each = contains(local.ma_filters, "rai") ? [1] : []
      content {
        dynamic "rai_filters" {
          for_each = ["HATE_SPEECH", "HARASSMENT", "SEXUALLY_EXPLICIT", "DANGEROUS"]
          content {
            filter_type      = rai_filters.value
            confidence_level = "MEDIUM_AND_ABOVE"
          }
        }
      }
    }
  }

  template_metadata {
    log_sanitize_operations                  = true
    log_template_operations                  = true
    custom_prompt_safety_error_code          = 403
    custom_prompt_safety_error_message       = "${var.model_armor_block_marker}: prompt blocked by Model Armor"
    custom_llm_response_safety_error_code    = 403
    custom_llm_response_safety_error_message = "${var.model_armor_block_marker}: response blocked by Model Armor"
  }

  depends_on = [google_project_service.api]
}

resource "google_network_services_authz_extension" "model_armor" {
  for_each = toset(distinct([var.project_id, var.gateway_project_id]))

  project   = each.value
  location  = var.region
  name      = var.names.model_armor_ext
  service   = "modelarmor.${var.region}.rep.googleapis.com"
  fail_open = false
  timeout   = "5s"
  labels    = var.labels
  metadata = {
    model_armor_settings = local.ma_settings
  }

  depends_on = [google_model_armor_template.shield]
}

resource "google_project_iam_member" "model_armor_agents" {
  for_each = local.ma_grants

  project = var.project_id
  role    = each.value.role
  member  = local.ma_agents[each.value.agent]

  # The gateway create provisions the service agents in a fresh project (plan §8 Q7).
  depends_on = [google_network_services_agent_gateway.gw]
}
