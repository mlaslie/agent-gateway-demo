# Terraform migration runbook (phase T2, live steps)

Adopts the live, bootstrap-created demo environment into Terraform with **no resource recreated**, moves the recordings to their own surviving bucket, and retires `bootstrap`. Background: [TERRAFORM_PLAN.md](TERRAFORM_PLAN.md) §5 (migration), §7a (T1), §7b (decisions), §7d (T2 read-only results).

Run the steps **in order**, in a quiet window: nobody presenting, no Policy Studio or UI policy changes while the steps run. Every step names its expected output. **If an output differs, stop** and compare with the read-only results in TERRAFORM_PLAN.md §7d before going on.

Placeholders: project `acme-prod`, project number `123456789012`, region `us-east4`, prefix `agdemo`. Set them once per shell:

```bash
export PROJECT=acme-prod REGION=us-east4
```

`gcloud` must be on `PATH` (or use its full path). Terraform >= 1.7. Terraform uses Application Default Credentials (`gcloud auth application-default login`) with the same account that ran `bootstrap` (project owner).

## 0. Before you start

1. Clean working tree and current code: `git status` shows nothing to commit.
2. Back up the deploy state:

   ```bash
   mkdir -p config/generated/tf-migration
   ```

   ```bash
   cp config/state.json config/generated/tf-migration/state.json.before
   ```

3. Read-only baseline (keep the output to compare in step g):

   ```bash
   ./agdemo status > config/generated/tf-migration/status.before.txt
   ```

The read-only phase saved the expected plan in `config/generated/tf-migration/` (git-ignored): `plan.txt`, `plan.json`, `plan-actions.txt` and `snapshot/` (GETs of every resource). Step c compares against `plan-actions.txt`.

## a. Delete the legacy resources

Terraform never adopts these (plan §2.3, Q4). The read-only check found **no references and nothing in use**:

| Resource | What references it | Why deleting is safe |
|---|---|---|
| authz policy `agdemo-ingress-iap-policy` (REQUEST_AUTHZ on `agdemo-ingress`, provider `agdemo-ingress-iap-authz`) | Only `teardown` (cleanup by name) | A CLIENT_TO_AGENT gateway doesn't evaluate REQUEST_AUTHZ (ARCHITECTURE.md §10, ISSUES.md #1): no behaviour change. Not in Terraform, not shown by `status` |
| authz extension `agdemo-ingress-iap-authz` | Only the legacy policy above | Nothing else points at it |
| SAs `agdemo-denied-caller`, `agdemo-allowed-caller` | Only `teardown` (cleanup by name). No Cloud Run service, engine or registry IAP policy uses them | Only Google-managed keys; last use was the old ingress test (2026-10-08, audit log). Grants to remove: project `roles/aiplatform.user` each. Their own IAM (`serviceAccountTokenCreator` for the UI SA and the operator) goes with them |

Delete the policy first (the extension can't be deleted while a policy uses it). Each delete waits for its operation (about 1–3 min).

```bash
gcloud network-security authz-policies delete agdemo-ingress-iap-policy --location=$REGION --project=$PROJECT --quiet
```

```bash
gcloud service-extensions authz-extensions delete agdemo-ingress-iap-authz --location=$REGION --project=$PROJECT --quiet
```

Remove the project grants before the SAs, so no `deleted:serviceAccount:` member is left in the project policy:

```bash
gcloud projects remove-iam-policy-binding $PROJECT --member=serviceAccount:agdemo-denied-caller@$PROJECT.iam.gserviceaccount.com --role=roles/aiplatform.user --condition=None --format=none
```

```bash
gcloud projects remove-iam-policy-binding $PROJECT --member=serviceAccount:agdemo-allowed-caller@$PROJECT.iam.gserviceaccount.com --role=roles/aiplatform.user --condition=None --format=none
```

```bash
gcloud iam service-accounts delete agdemo-denied-caller@$PROJECT.iam.gserviceaccount.com --project=$PROJECT --quiet
```

```bash
gcloud iam service-accounts delete agdemo-allowed-caller@$PROJECT.iam.gserviceaccount.com --project=$PROJECT --quiet
```

**Verify each one is gone:**

```bash
gcloud network-security authz-policies list --location=$REGION --project=$PROJECT --format="value(name.basename())"
```

Expected: `agdemo-egress-iap-policy` only (plus `agdemo-{egress,ingress}-ma-policy` if the Model Armor checkbox is on).

```bash
gcloud service-extensions authz-extensions list --location=$REGION --project=$PROJECT --format="value(name.basename())"
```

Expected: `agdemo-egress-iap-authz` and `agdemo-ma-authz` only.

```bash
gcloud iam service-accounts list --project=$PROJECT --filter="email~caller" --format="value(email)"
```

```bash
gcloud projects get-iam-policy $PROJECT --flatten="bindings[].members" --filter="bindings.members~caller" --format="value(bindings.role,bindings.members)"
```

Expected: both print nothing.

## b. `./agdemo tf init` against GCS (creates the state bucket)

```bash
./agdemo tf init
```

Expected:

```
  created state bucket gs://acme-prod-agdemo-tfstate (versioning on)
Terraform has been successfully initialized!
initialized state: gs://acme-prod-agdemo-tfstate/agdemo/acme-prod/us-east4
```

It removes a `backend_override.tf` left by a scratch `--local-state` run, and the scratch state is not migrated (it never held anything applied). Verify:

```bash
gcloud storage buckets describe gs://$PROJECT-agdemo-tfstate --format="value(location,versioning_enabled,uniform_bucket_level_access)"
```

Expected: `US-EAST4  True  True`.

## c. Generate the imports, plan, and compare with the saved plan

```bash
./agdemo tf import
```

Expected: no `legacy resource still present` and no `not live, Terraform will create it` lines, then:

```
  wrote .../infra/terraform/imports.tf
35 imports (10 blocks + for_each over 25 platform entries)
```

`imports.tf` is generated and git-ignored (it holds the real project id); see TERRAFORM_PLAN.md §7d.

```bash
./agdemo tf plan --out config/generated/tf-migration/live.tfplan --json config/generated/tf-migration/live.json
```

Expected:

```
plan: 68 to add, 8 to change, 0 to replace, 0 to destroy, 35 to import (migration mode: imports.tf present)
plan-safety: OK
```

Migration mode refuses any create that isn't an additive IAM grant or API enablement, so a missing import can't slip through. Compare the actions with the saved plan:

```bash
jq -r '.resource_changes[] | select(.mode=="managed") | "\(if .change.importing then "import+" else "" end)\(.change.actions|join("/")) \(.address)"' config/generated/tf-migration/live.json | sort > config/generated/tf-migration/live-actions.txt
```

```bash
diff config/generated/tf-migration/plan-actions.txt config/generated/tf-migration/live-actions.txt && echo SAME
```

Expected: `SAME`. By type:

| Action | Type | Count |
|---|---|---|
| import, no change | `google_agent_registry_service` (platform entries) | 25 |
| import, no change | `google_service_account` | 2 |
| import + update (state only, see below) | `google_network_services_agent_gateway` 2, `google_network_services_authz_extension` 2, `google_network_security_authz_policy` 1, `google_model_armor_template` 1, `google_artifact_registry_repository` 1, `google_storage_bucket` 1 | 8 |
| create (additive, already live) | `google_project_service` | 21 |
| create (additive, already live) | `google_project_iam_member` (UI SA 9, targets SA 2, presenters 1, Model Armor roles × 3 service agents 9) | 21 |
| create (additive, already live) | `google_iap_agent_registry_endpoint_iam_member` (`roles/iap.egressor` on the 25 platform entries) | 25 |
| create (new) | `google_storage_bucket_iam_member` (UI SA `roles/storage.objectUser` on the recordings bucket) | 1 |

**The 8 in-place updates are harmless:**
- **`labels` / `terraform_labels` (all 8):** an import leaves `labels` empty in state (the provider only reads back keys the configuration has), so Terraform "adds" them. `effective_labels`, the field the provider compares to build the update mask, is unchanged, so the update mask is empty and **no API call is made** (the provider skips the PATCH when the mask is empty). The live labels already equal the configured ones.
- **`timeouts` (2 gateways):** Terraform-only setting.
- **`force_destroy` false → true (staging bucket):** Terraform-only setting (lets `down` delete the bucket with engine pickles in it). The bucket PATCH it triggers carries no field changes.

If the counts differ: a `create` of anything else means a resource is missing live or the import didn't match (the safety check refuses it); a `delete` or replace is refused too. A different presenter list (`ui.admin_access`) or a re-registered platform URL can legitimately change the create counts; anything else, stop.

## d. Recordings bucket: create, copy (not move), verify

```bash
./agdemo tf recordings-bucket
```

Expected: `gs://acme-prod-agdemo-recordings (created)`. Same call `tf apply` makes first: regional, uniform access, the demo labels, outside Terraform so `down` keeps it.

```bash
gcloud storage cp -r gs://$PROJECT-agdemo-staging/recordings gs://$PROJECT-agdemo-recordings/
```

Verify the counts and sizes match (20 objects, about 50 KB at the 2026-10-16 snapshot):

```bash
gcloud storage ls "gs://$PROJECT-agdemo-staging/recordings/**" | wc -l
```

```bash
gcloud storage ls "gs://$PROJECT-agdemo-recordings/recordings/**" | wc -l
```

```bash
gcloud storage du -s gs://$PROJECT-agdemo-staging/recordings gs://$PROJECT-agdemo-recordings/recordings
```

Expected: equal counts and equal byte totals. The staging copies stay where they are (the running UI still reads them until step f).

## e. Apply, output-sync, then a plan that must be 0/0/0

```bash
./agdemo tf apply
```

It re-runs the plan and the migration-mode check, prints the same summary as step c, and asks `Apply to acme-prod/us-east4 (prefix agdemo)?`. Answer `y`. There is no `created recordings bucket` line (step d made it). Expected end:

```
Apply complete! Resources: 35 imported, 68 added, 8 changed, 0 destroyed.
merged 16 shared keys into config/state.json
```

It takes a few minutes at most: no gateway or authz LROs run (the 8 updates send no PATCH); the time is the IAM read-modify-writes. `apply` already runs `output-sync`; running it again is idempotent:

```bash
./agdemo tf output-sync
```

Only two keys may change in `state.json shared`: `recordings_bucket` (new) and `tf_environment` (new). Check:

```bash
python3 -c "import json;a=json.load(open('config/generated/tf-migration/state.json.before'))['shared'];b=json.load(open('config/state.json'))['shared'];print(sorted(k for k in set(a)|set(b) if a.get(k)!=b.get(k)))"
```

Expected: `['recordings_bucket', 'tf_environment']`.

Remove the generated imports and plan again:

```bash
rm infra/terraform/imports.tf
```

```bash
./agdemo tf plan
```

Expected:

```
plan: 0 to add, 0 to change, 0 to replace, 0 to destroy
plan-safety: OK
```

(Terraform itself prints `No changes. Your infrastructure matches the configuration.`) From now on `./agdemo bootstrap` refuses this environment unless `--legacy` is passed (state.json has `tf_environment`).

## f. Redeploy the UI and verify it uses the recordings bucket

```bash
./agdemo ui deploy
```

```bash
gcloud run services describe agdemo-ui --region=$REGION --project=$PROJECT --format=json | jq -r '.spec.template.spec.containers[0].env[] | select(.name=="AGDEMO_RECORDINGS_URI") | .value'
```

Expected: `gs://acme-prod-agdemo-recordings/recordings`.

In the UI (Demo or Live + fallback mode), run a test that has a recording (any of the 20, e.g. helpdesk **Ask KB** with the KB grant applied): the timeline shows the replayed recording, not a simulation. Optionally record one test in Live mode and check it lands in the new bucket:

```bash
gcloud storage ls "gs://$PROJECT-agdemo-recordings/recordings/**" | wc -l
```

Expected: one more than in step d. If anyone recorded between steps d and f, copy the stragglers. `--no-clobber` never overwrites a newer recording already in the new bucket, and rsync without `--delete-unmatched-destination-objects` never deletes:

```bash
gcloud storage rsync -r --no-clobber gs://$PROJECT-agdemo-staging/recordings gs://$PROJECT-agdemo-recordings/recordings
```

## g. Status and Verify

```bash
./agdemo status
```

Expected: the shared table matches `status.before.txt` (both gateways, `REQUEST_AUTHZ` on `agdemo-egress-iap-policy`, 25 platform endpoints, the UI URL, the same theme engines and bindings). Then in the UI press **Verify** (header) for each enabled theme: every check passes as before.

## h. Rollback notes

Nothing in steps b–e recreates or deletes a resource, so rollback is about ownership, not infrastructure.

- **Never run `terraform destroy`** to undo the migration: it deletes the live gateways and everything else.
- **Before step e:** nothing to undo except step a. A deleted SA can be restored within 30 days (`gcloud iam service-accounts undelete <unique id>`; the id is in `config/generated/tf-migration/snapshot/sa_agdemo-*.json`). The legacy policy and extension can be re-created from their snapshot JSON, but they had no effect, so there's no reason to.
- **Apply failed halfway:** the imported resources stay in state and nothing was deleted. Fix the cause and re-run `./agdemo tf apply` (keep `imports.tf` until it succeeds).
- **Hand ownership back to bootstrap:** restore the old state.json and stop using Terraform; GCP is untouched:

  ```bash
  cp config/generated/tf-migration/state.json.before config/state.json
  ```

  Then `./agdemo ui deploy` (back to the staging recordings, which were copied, not moved). The Terraform state can stay in the bucket or be removed with `terraform state rm` per address (that never deletes resources).
- **A bad state write:** the state bucket is versioned; restore an earlier generation of `agdemo/acme-prod/us-east4/default.tfstate` with `gcloud storage cp` from its `#<generation>` URL.
- **The recordings bucket** is outside Terraform, so no rollback touches it. Delete it by hand only if you also go back to the staging recordings.
