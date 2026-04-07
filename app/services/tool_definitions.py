TOOL_GET_TODAYS_FOLLOWUPS = {
    "type": "function",
    "function": {
        "name": "get_todays_followups",
        "description": "Get all follow-ups scheduled for today for this business. Use when the user asks about today's tasks, pending follow-ups, or what they need to do today.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_GET_OVERDUE_FOLLOWUPS = {
    "type": "function",
    "function": {
        "name": "get_overdue_followups",
        "description": "Get all follow-ups that are past their scheduled date and still pending. Use when the user asks about overdue items, missed follow-ups, or pending tasks from previous days.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_GET_LEAD_DETAILS = {
    "type": "function",
    "function": {
        "name": "get_lead_details",
        "description": "Get full details of a specific lead including customer info, stage, estimated value, source, follow-up date, and notes. Use when the user asks about a specific lead's details or status.",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The UUID of the lead to fetch.",
                }
            },
            "required": ["lead_id"],
        },
    },
}

TOOL_GET_CUSTOMER_OUTSTANDING = {
    "type": "function",
    "function": {
        "name": "get_customer_outstanding",
        "description": "Get the outstanding payment summary for a specific customer. Use when the user asks about dues, outstanding balance, pending amount, or how much a customer owes.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The UUID of the customer.",
                }
            },
            "required": ["customer_id"],
        },
    },
}

TOOL_LIST_CUSTOMER_INVOICES = {
    "type": "function",
    "function": {
        "name": "list_customer_invoices",
        "description": "List invoices for a specific customer with invoice number, dates, totals, payment progress, and status. Use when the user asks to see invoices, bills, or billing history for a customer.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The UUID of the customer.",
                },
                "status": {
                    "type": "string",
                    "description": "Optional invoice status filter.",
                    "enum": ["paid", "partial", "sent", "approved", "draft"],
                },
            },
            "required": [],
        },
    },
}

TOOL_SEARCH_CUSTOMER = {
    "type": "function",
    "function": {
        "name": "search_customer",
        "description": "Search for customers by name or phone number. Use when the user mentions a customer by name or phone and you need to identify the matching customer record.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Customer name or phone number to search for.",
                }
            },
            "required": ["query"],
        },
    },
}

TOOL_SEARCH_LEAD = {
    "type": "function",
    "function": {
        "name": "search_lead",
        "description": "Search leads by title, customer name, or notes. Use when the user mentions a lead or enquiry and you need to identify the matching lead record.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Lead title, customer name, or keyword to search for.",
                }
            },
            "required": ["query"],
        },
    },
}

TOOL_GET_DASHBOARD_SUMMARY = {
    "type": "function",
    "function": {
        "name": "get_dashboard_summary",
        "description": "Get a business snapshot with today's follow-ups count, overdue follow-ups count, total outstanding amount, and recent leads. Use when the user asks for an overview or business summary.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_LIST_CUSTOMER_PAYMENTS = {
    "type": "function",
    "function": {
        "name": "list_customer_payments",
        "description": "List payment history for a specific customer with dates, amounts, methods, references, and related invoice IDs. Use when the user asks about payment history or payments received from a customer.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The UUID of the customer.",
                }
            },
            "required": [],
        },
    },
}

TOOL_GET_LEAD_FOLLOWUPS = {
    "type": "function",
    "function": {
        "name": "get_lead_followups",
        "description": "Get follow-up history for a specific lead with scheduled dates, status, completion status, and notes. Use when the user asks about follow-up history or next actions for a lead.",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The UUID of the lead.",
                }
            },
            "required": [],
        },
    },
}

TOOL_GET_CATALOG_ITEMS = {
    "type": "function",
    "function": {
        "name": "get_catalog_items",
        "description": "Get catalog items with names, descriptions, rates, tax, and units. Use when the user asks about products, services, catalog items, pricing, or available offerings.",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Optional search term to filter catalog items by name.",
                }
            },
            "required": [],
        },
    },
}

TOOL_CREATE_LEAD = {
    "type": "function",
    "function": {
        "name": "create_lead",
        "description": "Prepare a new lead or enquiry for creation. Use when the user wants to add a new lead, new enquiry, or save a new potential customer. Always use this tool for new lead creation requests.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Customer's full name."},
                "phone": {"type": "string", "description": "Customer phone number."},
                "requirement": {
                    "type": "string",
                    "description": "What the customer needs or is enquiring about.",
                },
                "source": {
                    "type": "string",
                    "description": "How the lead came in. Use 'instagram' for Instagram/social media leads, 'justdial' for JustDial leads.",
                    "enum": ["walk_in", "whatsapp", "referral", "instagram", "justdial", "website", "other"],
                },
                "estimated_value": {
                    "type": "number",
                    "description": "Estimated value in INR if known.",
                },
                "notes": {
                    "type": "string",
                    "description": "Any additional notes or context for the lead.",
                },
            },
            "required": ["name", "phone"],
        },
    },
}

TOOL_UPDATE_LEAD_STAGE = {
    "type": "function",
    "function": {
        "name": "update_lead_stage",
        "description": "Prepare a lead stage update. Use when the user wants to move a lead to another stage, mark status progress, or move a lead to won or lost.",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The UUID of the lead to update.",
                },
                "stage_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The UUID of the target stage.",
                },
            },
            "required": ["stage_id"],
        },
    },
}

TOOL_SCHEDULE_FOLLOWUP = {
    "type": "function",
    "function": {
        "name": "schedule_followup",
        "description": "Prepare a follow-up scheduling action for a lead. Use when the user wants to set a reminder, schedule a call, or plan the next contact for a lead.",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The UUID of the lead for this follow-up.",
                },
                "scheduled_date": {
                    "type": "string",
                    "description": "Date for the follow-up in YYYY-MM-DD format.",
                },
                "note": {
                    "type": "string",
                    "description": "What the follow-up is about.",
                },
            },
            "required": ["scheduled_date"],
        },
    },
}

TOOL_COMPLETE_FOLLOWUP = {
    "type": "function",
    "function": {
        "name": "complete_followup",
        "description": (
            "Mark a follow-up as completed with an optional outcome note. "
            "Use when user says 'call ho gaya', 'done', 'mark complete', 'follow-up ho gaya', "
            "'Rajesh se baat hui'. The followup_id can come from context or from previous tool results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "followup_id": {
                    "type": "string",
                    "description": "UUID of the follow-up to mark complete.",
                },
                "outcome_note": {
                    "type": "string",
                    "description": (
                        "What happened during the follow-up. E.g. 'Discussed pricing, will send quote tomorrow'."
                    ),
                },
            },
            "required": ["followup_id"],
        },
    },
}

TOOL_RESCHEDULE_FOLLOWUP = {
    "type": "function",
    "function": {
        "name": "reschedule_followup",
        "description": (
            "Reschedule an existing follow-up to a new date/time. "
            "Use when user says 'kal pe shift karo', 'reschedule', 'postpone', "
            "'move to tomorrow', 'next week pe daalo'. The followup_id comes from context or previous tool results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "followup_id": {
                    "type": "string",
                    "description": "UUID of the follow-up to reschedule.",
                },
                "new_date": {
                    "type": "string",
                    "description": "New date in YYYY-MM-DD format.",
                },
                "new_time": {
                    "type": "string",
                    "description": "New time in HH:MM format (24hr). Optional; keeps existing time if omitted.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for rescheduling.",
                },
            },
            "required": ["followup_id", "new_date"],
        },
    },
}

TOOL_BULK_UPDATE_FOLLOWUPS = {
    "type": "function",
    "function": {
        "name": "bulk_update_followups",
        "description": (
            "Update multiple follow-ups at once. Can mark them as completed/cancelled or reschedule them. "
            "Use only after first showing the affected items with get_todays_followups, get_overdue_followups, "
            "or get_stale_followups."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "What to do with the matched follow-ups.",
                    "enum": ["complete", "cancel", "reschedule"],
                },
                "filter_type": {
                    "type": "string",
                    "description": "How to filter follow-ups to update.",
                    "enum": ["today", "overdue", "date_range", "customer", "lead", "ids"],
                },
                "filter_value": {
                    "type": "string",
                    "description": (
                        "Value for the filter. date_range uses 'YYYY-MM-DD,YYYY-MM-DD'. "
                        "customer and lead expect UUIDs. ids uses comma-separated follow-up UUIDs."
                    ),
                },
                "reschedule_to_date": {
                    "type": "string",
                    "description": "New date (YYYY-MM-DD) when action=reschedule.",
                },
                "reschedule_to_time": {
                    "type": "string",
                    "description": "New time (HH:MM) when action=reschedule.",
                },
                "note": {
                    "type": "string",
                    "description": "Note to apply to all matched follow-ups.",
                },
            },
            "required": ["action", "filter_type"],
        },
    },
}

TOOL_GET_STALE_FOLLOWUPS = {
    "type": "function",
    "function": {
        "name": "get_stale_followups",
        "description": (
            "Get old pending follow-ups that are likely stale and need cleanup. "
            "Use before bulk_update_followups to show the user what will be affected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "older_than_days": {
                    "type": "integer",
                    "description": "Show follow-ups older than this many days. Default 7.",
                },
                "customer_id": {
                    "type": "string",
                    "description": "Optional customer UUID filter.",
                },
                "lead_id": {
                    "type": "string",
                    "description": "Optional lead UUID filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results. Default 30.",
                },
            },
            "required": [],
        },
    },
}

TOOL_PREPARE_INVOICE = {
    "type": "function",
    "function": {
        "name": "prepare_invoice",
        "description": (
            "Prepare an invoice for a customer. Use when the user wants to create, generate, "
            "or make an invoice. The user may specify items using @mentions, which include "
            "catalog_item_id, default_rate, unit, gst_percent, and description in the "
            "MENTIONED ENTITIES context, or describe items as free text."
            "\n\nIMPORTANT RULES FOR LINE ITEMS:"
            "\n- If the user @mentioned a catalog item, use its catalog_item_id, default_rate, "
            "unit, gst_percent, and description from MENTIONED ENTITIES."
            "\n- If the user explicitly states a different price, use the user's price instead "
            "of the default rate."
            "\n- If the user explicitly states a different unit, use the user's unit."
            "\n- If the user mentions an item without an @mention, set catalog_item_id to null."
            "\n- If quantity is not specified, default to 1."
            "\n- If GST is not specified and the item is not from catalog, default to 18%."
            "\n- Prefer lead_id from page context when available because invoices are linked to leads."
            "\n\nFor customer: use customer_id from MENTIONED ENTITIES if the user @mentioned "
            "a customer. If on a lead page, use the lead's customer. If the user provides just "
            "a name, search for the customer first using search_customer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "UUID of the customer from @mention, page context, or search_customer.",
                },
                "customer_name": {
                    "type": "string",
                    "description": "Customer display name for form display.",
                },
                "lead_id": {
                    "type": "string",
                    "description": "UUID of the associated lead if known.",
                },
                "due_date": {
                    "type": "string",
                    "description": "Invoice due date in YYYY-MM-DD format. Default to 15 days from today if omitted.",
                },
                "items": {
                    "type": "array",
                    "description": "Invoice line items.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "catalog_item_id": {"type": "string"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "rate": {"type": "number"},
                            "unit": {"type": "string"},
                            "gst_percent": {"type": "number"},
                        },
                        "required": ["name", "quantity", "rate"],
                    },
                },
                "notes": {
                    "type": "string",
                    "description": "Optional invoice notes for the confirmation form.",
                },
            },
            "required": ["customer_id", "customer_name", "items", "due_date"],
        },
    },
}

TOOL_QUERY_INVOICES = {
    "type": "function",
    "function": {
        "name": "query_invoices",
        "description": (
            "Query invoices with flexible filters and return both invoice results and billing aggregates. "
            "Use for listing invoices by period, payment status, customer, lead, amount range, or search. "
            "Combine filters from the current conversation when the user drills down further."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Date range preset for invoice issued_date.",
                    "enum": [
                        "today",
                        "this_week",
                        "this_month",
                        "last_month",
                        "last_2_months",
                        "last_quarter",
                        "this_year",
                        "last_30_days",
                        "last_90_days",
                        "custom",
                    ],
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD when period=custom.",
                },
                "to_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD when period=custom.",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by invoice status.",
                    "enum": ["draft", "sent", "approved", "paid", "partial"],
                },
                "customer_id": {
                    "type": "string",
                    "description": "Filter by customer UUID.",
                },
                "lead_id": {
                    "type": "string",
                    "description": "Filter by lead UUID.",
                },
                "min_amount": {
                    "type": "number",
                    "description": "Minimum invoice total amount.",
                },
                "max_amount": {
                    "type": "number",
                    "description": "Maximum invoice total amount.",
                },
                "payment_status": {
                    "type": "string",
                    "description": "Computed payment state based on amount paid versus invoice total.",
                    "enum": ["unpaid", "partially_paid", "fully_paid"],
                },
                "search": {
                    "type": "string",
                    "description": "Search in invoice number, customer name, lead title, or line item names.",
                },
                "sort_by": {
                    "type": "string",
                    "description": "How to sort results.",
                    "enum": ["date", "amount", "due_date", "outstanding"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Default 20.",
                },
            },
            "required": [],
        },
    },
}

TOOL_UPDATE_INVOICE = {
    "type": "function",
    "function": {
        "name": "update_invoice",
        "description": (
            "Prepare an invoice update for user review. Use when the user wants to: mark an estimate as sent, "
            "mark an estimate/invoice as approved (client has approved the quote), change due date, "
            "or edit draft invoice line items. "
            "Approve flow: 'Rajesh ka estimate approve karo' → set new_status='approved'. "
            "Sent flow: 'INV-042 sent mark karo' → set new_status='sent'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "UUID of the invoice to update.",
                },
                "invoice_number": {
                    "type": "string",
                    "description": "Invoice number for display.",
                },
                "new_status": {
                    "type": "string",
                    "description": "New status to set. Use 'sent' to mark draft as sent. Use 'approved' when client approves the estimate — this converts it to a tax invoice.",
                    "enum": ["sent", "approved"],
                },
                "new_due_date": {
                    "type": "string",
                    "description": "New due date in YYYY-MM-DD format.",
                },
                "add_items": {
                    "type": "array",
                    "description": "New line items to add to the invoice.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "catalog_item_id": {"type": "string"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "rate": {"type": "number"},
                            "unit": {"type": "string"},
                            "gst_percent": {"type": "number"},
                        },
                        "required": ["name", "quantity", "rate"],
                    },
                },
                "update_items": {
                    "type": "array",
                    "description": "Existing items to modify, matched by item_name or item_index.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string"},
                            "item_index": {"type": "integer"},
                            "new_rate": {"type": "number"},
                            "new_quantity": {"type": "number"},
                            "new_name": {"type": "string"},
                            "new_unit": {"type": "string"},
                            "new_gst_percent": {"type": "number"},
                            "new_description": {"type": "string"},
                        },
                    },
                },
                "remove_item_names": {
                    "type": "array",
                    "description": "Names of items to remove from the invoice.",
                    "items": {"type": "string"},
                },
            },
            "required": ["invoice_id"],
        },
    },
}

TOOL_GET_BILLING_ANALYTICS = {
    "type": "function",
    "function": {
        "name": "get_billing_analytics",
        "description": (
            "Get invoice and revenue analytics for the business, with optional comparison and grouping. "
            "Use for billing summary, average invoice value, top customers, top items, monthly breakdown, or status mix."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Time period for analysis.",
                    "enum": [
                        "this_month",
                        "last_month",
                        "this_quarter",
                        "last_quarter",
                        "last_3_months",
                        "last_6_months",
                        "this_year",
                    ],
                },
                "compare_with": {
                    "type": "string",
                    "description": "Optional comparison period.",
                    "enum": ["previous_period"],
                },
                "group_by": {
                    "type": "string",
                    "description": "Optional grouping dimension.",
                    "enum": ["customer", "item", "month", "status"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum grouped rows to return. Default 10.",
                },
            },
            "required": ["period"],
        },
    },
}

TOOL_GET_INVOICE_PAYMENT_HISTORY = {
    "type": "function",
    "function": {
        "name": "get_invoice_payment_history",
        "description": (
            "Get the payment history for a specific invoice, including dates, amounts, methods, and references. "
            "Use when the user asks when payments were received on an invoice or asks for payment history."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "UUID of the invoice.",
                }
            },
            "required": ["invoice_id"],
        },
    },
}

TOOL_GENERATE_INVOICE_PDF = {
    "type": "function",
    "function": {
        "name": "generate_invoice_pdf",
        "description": (
            "Generate a PDF for an existing invoice and return the download URL. "
            "Use when the user says generate PDF, download invoice, share invoice, "
            "send invoice PDF, or after an invoice has just been created and wants the PDF."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "UUID of the invoice to generate a PDF for.",
                }
            },
            "required": ["invoice_id"],
        },
    },
}

TOOL_GET_INVOICE_DETAILS = {
    "type": "function",
    "function": {
        "name": "get_invoice_details",
        "description": (
            "Get full details of a specific invoice including line items, payment status, "
            "and amounts. Use when the user asks about a specific invoice, wants to see invoice "
            "details, check payment status of an invoice, or references an invoice by number or ID."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "UUID of the invoice. Get from @mention context or conversation history.",
                }
            },
            "required": ["invoice_id"],
        },
    },
}

TOOL_RECORD_PAYMENT = {
    "type": "function",
    "function": {
        "name": "record_payment",
        "description": (
            "Record a payment received against an invoice. Use when the user says payment received, "
            "record payment, full payment, or mentions receiving money for an invoice. "
            "Requires invoice_id and amount."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "UUID of the invoice this payment is against. From @mention or context.",
                },
                "invoice_number": {
                    "type": "string",
                    "description": "Invoice number for display such as INV-007.",
                },
                "amount": {
                    "type": "number",
                    "description": "Payment amount in INR. For full payment, use the invoice balance due amount.",
                },
                "payment_method": {
                    "type": "string",
                    "description": "How the payment was made.",
                    "enum": ["upi", "cash", "bank_transfer", "card"],
                },
                "reference": {
                    "type": "string",
                    "description": "Transaction reference ID, UPI reference, cheque number, etc.",
                },
                "payment_date": {
                    "type": "string",
                    "description": "Date payment was received in YYYY-MM-DD format. Default to today if omitted.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional notes about the payment for confirmation context.",
                },
            },
            "required": ["invoice_id", "amount"],
        },
    },
}

TOOL_LIST_CUSTOMERS_BY_OUTSTANDING = {
    "type": "function",
    "function": {
        "name": "list_customers_by_outstanding",
        "description": (
            "Get a list of customers sorted by outstanding amount highest first. "
            "Use when the user asks who owes the most money, top outstanding customers, "
            "pending payments list, or collection list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of customers to return. Default 10.",
                }
            },
            "required": [],
        },
    },
}

TOOL_LIST_LEADS_BY_STAGE = {
    "type": "function",
    "function": {
        "name": "list_leads_by_stage",
        "description": (
            "List leads filtered by pipeline stage and return the count. "
            "Use when the user asks for leads in a specific stage or how many leads are in a stage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "stage_name": {
                    "type": "string",
                    "description": "Name of the pipeline stage to filter by.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max leads to return. Default 20.",
                },
            },
            "required": ["stage_name"],
        },
    },
}

TOOL_GET_PIPELINE_SUMMARY = {
    "type": "function",
    "function": {
        "name": "get_pipeline_summary",
        "description": (
            "Get a summary of leads across all pipeline stages with count and total value per stage. "
            "Use when the user asks for a pipeline overview, funnel, or stage-wise count."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_LIST_OVERDUE_INVOICES = {
    "type": "function",
    "function": {
        "name": "list_overdue_invoices",
        "description": (
            "List invoices that are past their due date and not fully paid. "
            "Use when the user asks about overdue invoices, late payments, or pending collections."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max results. Default 20.",
                }
            },
            "required": [],
        },
    },
}

TOOL_GET_UNPAID_INVOICE_SUMMARY = {
    "type": "function",
    "function": {
        "name": "get_unpaid_invoice_summary",
        "description": (
            "Get aggregate summary of all unpaid invoices including total count, total collected, "
            "total outstanding, and overdue count. Use for a collections overview."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_GET_RECENT_PAYMENTS = {
    "type": "function",
    "function": {
        "name": "get_recent_payments",
        "description": (
            "Get recent payments received. Use when the user asks about today's payments, "
            "recent payments, or payments received over the last few days."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Look back this many days. Default 7.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results. Default 20.",
                },
            },
            "required": [],
        },
    },
}

TOOL_GET_REVENUE_SUMMARY = {
    "type": "function",
    "function": {
        "name": "get_revenue_summary",
        "description": (
            "Get revenue collected for a given time period. Use when the user asks about "
            "today's revenue, this week's revenue, this month, last month, or recent collections."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Time period to summarize.",
                    "enum": ["today", "this_week", "this_month", "last_month", "last_30_days", "last_90_days"],
                }
            },
            "required": ["period"],
        },
    },
}

TOOL_SEND_PAYMENT_REMINDER = {
    "type": "function",
    "function": {
        "name": "send_payment_reminder",
        "description": (
            "Prepare a payment reminder message to send to a customer via WhatsApp. "
            "Use when the user wants to remind a customer about pending payment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "UUID of the customer to remind."},
                "customer_name": {"type": "string", "description": "Customer name for display."},
                "customer_phone": {"type": "string", "description": "Customer phone number."},
                "outstanding_amount": {"type": "number", "description": "Total outstanding amount."},
                "invoice_numbers": {"type": "string", "description": "Comma-separated unpaid invoice numbers."},
                "message_tone": {
                    "type": "string",
                    "description": "Tone of the reminder.",
                    "enum": ["polite", "firm", "urgent"],
                },
            },
            "required": ["customer_name", "customer_phone", "outstanding_amount"],
        },
    },
}

TOOL_ADD_LEAD_NOTE = {
    "type": "function",
    "function": {
        "name": "add_lead_note",
        "description": (
            "Add a note to a lead's activity log. Use when the user says note, add note, "
            "remember that, mark that, or provides contextual information about a lead "
            "that should be saved."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "description": "UUID of the lead. Use page context when on a lead page.",
                },
                "note": {
                    "type": "string",
                    "description": "The note content to save.",
                },
            },
            "required": ["lead_id", "note"],
        },
    },
}

DASHBOARD_TOOLS = [
    TOOL_GET_TODAYS_FOLLOWUPS,
    TOOL_GET_OVERDUE_FOLLOWUPS,
    TOOL_GET_DASHBOARD_SUMMARY,
    TOOL_SEARCH_CUSTOMER,
    TOOL_SEARCH_LEAD,
    TOOL_CREATE_LEAD,
    TOOL_GET_CATALOG_ITEMS,
    TOOL_LIST_CUSTOMER_INVOICES,
    TOOL_LIST_LEADS_BY_STAGE,
    TOOL_GET_PIPELINE_SUMMARY,
    TOOL_GET_INVOICE_DETAILS,
    TOOL_LIST_CUSTOMERS_BY_OUTSTANDING,
    TOOL_LIST_OVERDUE_INVOICES,
    TOOL_GET_UNPAID_INVOICE_SUMMARY,
    TOOL_GET_RECENT_PAYMENTS,
    TOOL_GET_REVENUE_SUMMARY,
    TOOL_QUERY_INVOICES,
    TOOL_GET_BILLING_ANALYTICS,
    TOOL_PREPARE_INVOICE,
    TOOL_GENERATE_INVOICE_PDF,
    TOOL_SEND_PAYMENT_REMINDER,
    TOOL_SCHEDULE_FOLLOWUP,
    TOOL_COMPLETE_FOLLOWUP,
    TOOL_RESCHEDULE_FOLLOWUP,
    TOOL_BULK_UPDATE_FOLLOWUPS,
    TOOL_GET_STALE_FOLLOWUPS,
]

CUSTOMER_TOOLS = [
    TOOL_GET_CUSTOMER_OUTSTANDING,
    TOOL_LIST_CUSTOMER_INVOICES,
    TOOL_QUERY_INVOICES,
    TOOL_UPDATE_INVOICE,
    TOOL_GET_INVOICE_PAYMENT_HISTORY,
    TOOL_LIST_CUSTOMER_PAYMENTS,
    TOOL_SEARCH_LEAD,
    TOOL_CREATE_LEAD,
    TOOL_GET_CATALOG_ITEMS,
    TOOL_GET_INVOICE_DETAILS,
    TOOL_RECORD_PAYMENT,
    TOOL_LIST_CUSTOMERS_BY_OUTSTANDING,
    TOOL_LIST_OVERDUE_INVOICES,
    TOOL_GET_RECENT_PAYMENTS,
    TOOL_SEND_PAYMENT_REMINDER,
    TOOL_PREPARE_INVOICE,
    TOOL_GENERATE_INVOICE_PDF,
    TOOL_SCHEDULE_FOLLOWUP,
    TOOL_COMPLETE_FOLLOWUP,
    TOOL_RESCHEDULE_FOLLOWUP,
    TOOL_BULK_UPDATE_FOLLOWUPS,
    TOOL_GET_STALE_FOLLOWUPS,
]

LEAD_TOOLS = [
    TOOL_GET_LEAD_DETAILS,
    TOOL_GET_LEAD_FOLLOWUPS,
    TOOL_UPDATE_LEAD_STAGE,
    TOOL_SCHEDULE_FOLLOWUP,
    TOOL_COMPLETE_FOLLOWUP,
    TOOL_RESCHEDULE_FOLLOWUP,
    TOOL_BULK_UPDATE_FOLLOWUPS,
    TOOL_GET_STALE_FOLLOWUPS,
    TOOL_GET_CUSTOMER_OUTSTANDING,
    TOOL_GET_CATALOG_ITEMS,
    TOOL_LIST_CUSTOMER_INVOICES,
    TOOL_QUERY_INVOICES,
    TOOL_UPDATE_INVOICE,
    TOOL_GET_INVOICE_PAYMENT_HISTORY,
    TOOL_LIST_LEADS_BY_STAGE,
    TOOL_GET_INVOICE_DETAILS,
    TOOL_RECORD_PAYMENT,
    TOOL_PREPARE_INVOICE,
    TOOL_GENERATE_INVOICE_PDF,
    TOOL_ADD_LEAD_NOTE,
]

GLOBAL_TOOLS = [
    TOOL_SEARCH_CUSTOMER,
    TOOL_SEARCH_LEAD,
    TOOL_GET_TODAYS_FOLLOWUPS,
    TOOL_GET_OVERDUE_FOLLOWUPS,
    TOOL_GET_DASHBOARD_SUMMARY,
    TOOL_CREATE_LEAD,
    TOOL_GET_CATALOG_ITEMS,
    TOOL_LIST_CUSTOMER_INVOICES,
    TOOL_QUERY_INVOICES,
    TOOL_UPDATE_INVOICE,
    TOOL_GET_BILLING_ANALYTICS,
    TOOL_GET_INVOICE_PAYMENT_HISTORY,
    TOOL_LIST_LEADS_BY_STAGE,
    TOOL_GET_PIPELINE_SUMMARY,
    TOOL_GET_INVOICE_DETAILS,
    TOOL_RECORD_PAYMENT,
    TOOL_LIST_CUSTOMERS_BY_OUTSTANDING,
    TOOL_LIST_OVERDUE_INVOICES,
    TOOL_GET_UNPAID_INVOICE_SUMMARY,
    TOOL_GET_RECENT_PAYMENTS,
    TOOL_GET_REVENUE_SUMMARY,
    TOOL_SEND_PAYMENT_REMINDER,
    TOOL_PREPARE_INVOICE,
    TOOL_GENERATE_INVOICE_PDF,
    TOOL_ADD_LEAD_NOTE,
    TOOL_COMPLETE_FOLLOWUP,
    TOOL_RESCHEDULE_FOLLOWUP,
    TOOL_BULK_UPDATE_FOLLOWUPS,
    TOOL_GET_STALE_FOLLOWUPS,
]


def get_tools_for_context(context_type: str) -> list[dict]:
    mapping = {
        "dashboard": DASHBOARD_TOOLS,
        "customer": CUSTOMER_TOOLS,
        "lead": LEAD_TOOLS,
        "global": GLOBAL_TOOLS,
    }
    return mapping.get(context_type, GLOBAL_TOOLS)
