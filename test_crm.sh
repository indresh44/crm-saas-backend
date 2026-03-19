#!/bin/bash

# =============================================================================
# CRM API Test Script — Fully Sequential (auto-captures all IDs)
# Prerequisites: jq installed, server running on localhost:8000
# Run: chmod +x test_crm.sh && ./test_crm.sh
# =============================================================================

BASE="http://localhost:8000"

# ─── FILL THESE IN — your existing user + business ───────────────────────────
USER_ID="ba61cf8f-d920-4bc0-88d0-b2773fd60a9d"
BUSINESS_ID="6aaf7b0a-7d0b-44ce-841a-490670bacd59"
# ─────────────────────────────────────────────────────────────────────────────

PIPELINE_ID="2495164d-95df-4ddc-946c-f76616805d0e"
CUSTOMER_ID="6b192682-e87d-41be-95e1-656ae314c6ae"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
pass() { echo -e "${GREEN}  ✓ $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; }
info() { echo -e "${YELLOW}  → $1${NC}"; }

check_id() {
  if [ -z "$2" ] || [ "$2" = "null" ]; then
    fail "$1 — ID not captured. Stopping here."
    exit 1
  else
    pass "$1 = $2"
  fi
}

# =============================================================================
# STEP 1 — Confirm user is reachable
# =============================================================================
echo ""; echo "=== STEP 1: Verify User ==="
RESPONSE=$(curl -s "$BASE/api/v1/users/$USER_ID" -H "X-User-Id: $USER_ID")
echo "$RESPONSE" | jq .
check_id "User name" "$(echo "$RESPONSE" | jq -r '.name')"

# =============================================================================
# STEP 2 — Create Customer
# =============================================================================
# echo ""; echo "=== STEP 2: Create Customer ==="
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/customers" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d '{"name":"Ram Verma","phone":"9876543220","email":"ram.verma@example.com","notes":"Referred by Suresh bhai"}')
# echo "$RESPONSE" | jq .
# CUSTOMER_ID=$(echo "$RESPONSE" | jq -r '.id')
check_id "CUSTOMER_ID" "$CUSTOMER_ID"

# =============================================================================
# STEP 3A — Create Pipeline
# =============================================================================
# echo ""; echo "=== STEP 3A: Create Pipeline ==="
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/pipelines" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d '{"name":"Main Sales Pipeline"}')
# echo "$RESPONSE" | jq .
# PIPELINE_ID=$(echo "$RESPONSE" | jq -r '.id')
check_id "PIPELINE_ID" "$PIPELINE_ID"

# =============================================================================
# STEP 3B-3D — Create Stages
# =============================================================================
# echo ""; echo "=== STEP 3B: Stage Enquiry ==="
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/pipeline-stages" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d "{\"pipeline_id\":\"$PIPELINE_ID\",\"name\":\"Enquiry\",\"position\":1,\"color\":\"#6366f1\"}")
# echo "$RESPONSE" | jq .
# STAGE_ENQUIRY_ID=$(echo "$RESPONSE" | jq -r '.id')
# check_id "STAGE_ENQUIRY_ID" "$STAGE_ENQUIRY_ID"

# echo ""; echo "=== STEP 3C: Stage Interested ==="
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/pipeline-stages" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d "{\"pipeline_id\":\"$PIPELINE_ID\",\"name\":\"Interested\",\"position\":2,\"color\":\"#f59e0b\"}")
# echo "$RESPONSE" | jq .
# STAGE_INTERESTED_ID=$(echo "$RESPONSE" | jq -r '.id')
# check_id "STAGE_INTERESTED_ID" "$STAGE_INTERESTED_ID"

# echo ""; echo "=== STEP 3D: Stage Negotiation ==="
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/pipeline-stages" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d "{\"pipeline_id\":\"$PIPELINE_ID\",\"name\":\"Negotiation\",\"position\":3,\"color\":\"#10b981\"}")
# echo "$RESPONSE" | jq .
# STAGE_NEGOTIATION_ID=$(echo "$RESPONSE" | jq -r '.id')
# check_id "STAGE_NEGOTIATION_ID" "$STAGE_NEGOTIATION_ID"

# # =============================================================================
# # STEP 4 — Create Lead
# # =============================================================================
# echo ""; echo "=== STEP 4: Create Lead ==="
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/leads" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d "{
#     \"customer_id\":\"$CUSTOMER_ID\",
#     \"stage_id\":\"$STAGE_ENQUIRY_ID\",
#     \"title\":\"Interior Design - 3BHK Bhopal\",
#     \"source\":\"referral\",
#     \"service_date\":\"2026-04-15\",
#     \"estimated_value\":85000,
#     \"notes\":\"Client wants modern style, budget flexible\"
#   }")
# echo "$RESPONSE" | jq .
# LEAD_ID=$(echo "$RESPONSE" | jq -r '.id')
# check_id "LEAD_ID" "$LEAD_ID"

# # =============================================================================
# # STEP 5 — Move Lead Stage
# # =============================================================================
# echo ""; echo "=== STEP 5: Move Lead → Interested ==="
# curl -s -X POST "$BASE/api/v1/leads/$LEAD_ID/move" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d "{\"stage_id\":\"$STAGE_INTERESTED_ID\"}" | jq .

# # =============================================================================
# # STEP 6 — Log Activity
# # =============================================================c================
# echo ""; echo "=== STEP 6: Log Call Activity ==="
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/leads/$LEAD_ID/activities" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d "{\"lead_id\":\"$LEAD_ID\",\"type\":\"call\",\"description\":\"Called Ramesh. Interested. Will send moodboard.\"}")
# echo "$RESPONSE" | jq .

# # =============================================================================
# # STEP 7 — Follow-up #1 (today)
# # 404 here = lead_followups router not registered in main.py
# # =============================================================================
# echo ""; echo "=== STEP 7: Create Follow-up #1 (today) ==="
# TODAY=$(date -u +%Y-%m-%dT09:00:00Z)
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/lead_followups" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d "{\"lead_id\":\"$LEAD_ID\",\"scheduled_at\":\"$TODAY\",\"note\":\"Confirm site visit date\"}")
# echo "$RESPONSE" | jq .
# FOLLOWUP_1_ID=$(echo "$RESPONSE" | jq -r '.id')
# check_id "FOLLOWUP_1_ID" "$FOLLOWUP_1_ID"

# # =============================================================================
# # STEP 8 — Today's follow-ups (dashboard)
# # =============================================================================
# echo ""; echo "=== STEP 8: Today's Follow-ups ==="
# curl -s "$BASE/api/v1/lead_followups/today" -H "X-User-Id: $USER_ID" | jq .

# # =============================================================================
# # STEP 9 — Mark follow-up done
# # =============================================================================
# echo ""; echo "=== STEP 9: Mark Follow-up #1 Done ==="
# curl -s -X PATCH "$BASE/api/v1/lead_followups/$FOLLOWUP_1_ID/done" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d '{"note":"Site visit confirmed for March 22nd."}' | jq .

# # =============================================================================
# # STEP 10 — Follow-up #2 (chained, next week)
# # =============================================================================
# echo ""; echo "=== STEP 10: Create Follow-up #2 (next week) ==="
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/lead_followups" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d "{\"lead_id\":\"$LEAD_ID\",\"scheduled_at\":\"2026-03-24T09:00:00Z\",\"note\":\"Send estimate. Discuss payment terms.\"}")
# echo "$RESPONSE" | jq .
# FOLLOWUP_2_ID=$(echo "$RESPONSE" | jq -r '.id')
# check_id "FOLLOWUP_2_ID" "$FOLLOWUP_2_ID"

# # =============================================================================
# # STEP 11 — All follow-ups for lead (should show #1 done + #2 pending)
# # =============================================================================
# echo ""; echo "=== STEP 11: All Follow-ups for Lead ==="
# curl -s "$BASE/api/v1/lead_followups?lead_id=$LEAD_ID" -H "X-User-Id: $USER_ID" | jq .

# # =============================================================================
# # STEP 12 — Task linked to Lead
# # =============================================================================
# echo ""; echo "=== STEP 12: Create Task for Lead ==="
# RESPONSE=$(curl -s -X POST "$BASE/api/v1/tasks" \
#   -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
#   -d "{
#     \"lead_id\":\"$LEAD_ID\",
#     \"title\":\"Prepare moodboard for site visit\",
#     \"assigned_to\":\"$USER_ID\",
#     \"due_date\":\"2026-03-22\",
#     \"priority\":1,
#     \"status\":\"pending\"
#   }")
# echo "$RESPONSE" | jq .
# TASK_ID=$(echo "$RESPONSE" | jq -r '.id')
# check_id "TASK_ID" "$TASK_ID"

LEAD_ID="b67c123d-dd95-4c4d-9c54-7202cfdbcbfd"
# =============================================================================
# STEP 13 — Quote with line items
# NOTE: nested structure {"quote":{...},"items":[...]}
# =============================================================================
echo ""; echo "=== STEP 13: Create Quote with Items ==="
RESPONSE=$(curl -s -X POST "$BASE/api/v1/quotes" \
  -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
  -d "{
    \"quote\":{
      \"lead_id\":\"$LEAD_ID\",
      \"total_amount\":85000,
      \"status\":\"draft\",
      \"issued_date\":\"2026-03-17\",
      \"expires_date\":\"2026-03-31\",
      \"title\":\"Interior Design Quote\"
    },
    \"items\":[
      {\"description\":\"Interior Design Consultation\",\"quantity\":1,\"unit_price\":15000,\"gst_percent\":18},
      {\"description\":\"Living Room Design + Execution\",\"quantity\":1,\"unit_price\":55000,\"gst_percent\":18},
      {\"description\":\"Material Procurement\",\"quantity\":1,\"unit_price\":15000,\"gst_percent\":18}
    ]
  }")
echo "$RESPONSE" | jq .
QUOTE_ID=$(echo "$RESPONSE" | jq -r '.id')
check_id "QUOTE_ID" "$QUOTE_ID"

# =============================================================================
# STEP 14 — Invoice from Lead (no booking required)
# NOTE: nested structure {"invoice":{...},"items":[...]}
# =============================================================================
echo ""; echo "=== STEP 14: Create Invoice from Lead ==="
RESPONSE=$(curl -s -X POST "$BASE/api/v1/invoices" \
  -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
  -d "{
    \"invoice\":{
      \"lead_id\":\"$LEAD_ID\",
      \"total_amount\":25000,
      \"status\":\"sent\",
      \"issued_date\":\"2026-03-17\",
      \"due_date\":\"2026-03-25\",
      \"invoice_number\":\"INV-1001\"
    },
    \"items\":[
      {\"description\":\"Advance - Interior Design\",\"quantity\":1,\"unit_price\":25000,\"gst_percent\":18}
    ]
  }")
echo "$RESPONSE" | jq .
INVOICE_ID=$(echo "$RESPONSE" | jq -r '.id')
check_id "INVOICE_ID" "$INVOICE_ID"

# =============================================================================
# STEP 15 — Record Payment
# FIX: payment_method (not method), payment_date required
# =============================================================================
echo ""; echo "=== STEP 15: Record UPI Payment ==="
curl -s -X POST "$BASE/api/v1/payments" \
  -H "Content-Type: application/json" -H "X-User-Id: $USER_ID" \
  -d "{
    \"invoice_id\":\"$INVOICE_ID\",
    \"amount\":10000,
    \"payment_method\":\"upi\",
    \"payment_date\":\"2026-03-17\",
    \"reference\":\"UPI/260317/123456789\"
  }" | jq .

# =============================================================================
# STEP 16 — Outstanding balance
# 404 here = outstanding endpoint not registered yet (Prompt 5)
# =============================================================================
echo ""; echo "=== STEP 16: Customer Outstanding ==="
curl -s "$BASE/api/v1/customers/$CUSTOMER_ID/outstanding" \
  -H "X-User-Id: $USER_ID" | jq .

# =============================================================================
# STEP 17-18 — Search (already working)
# =============================================================================
echo ""; echo "=== STEP 17: Search Customer ==="
curl -s "$BASE/api/v1/customers/search?query=Ramesh" -H "X-User-Id: $USER_ID" | jq .

echo ""; echo "=== STEP 18: Customer by Phone ==="
curl -s "$BASE/api/v1/customers/by-phone?phone=9876543210" -H "X-User-Id: $USER_ID" | jq .

# =============================================================================
# STEP 19-20 — Lead list + detail
# =============================================================================
echo ""; echo "=== STEP 19: List All Leads ==="
curl -s "$BASE/api/v1/leads" -H "X-User-Id: $USER_ID" | jq .

echo ""; echo "=== STEP 20: Full Lead Detail ==="
curl -s "$BASE/api/v1/leads/$LEAD_ID" -H "X-User-Id: $USER_ID" | jq .

echo ""; echo "=== ALL STEPS COMPLETE ==="