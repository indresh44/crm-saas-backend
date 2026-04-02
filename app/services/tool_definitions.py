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
                    "enum": ["paid", "partial", "sent", "overdue", "draft"],
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
                    "description": "How the lead came in.",
                    "enum": ["walk_in", "referral", "whatsapp", "social_media", "website", "other"],
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

DASHBOARD_TOOLS = [
    TOOL_GET_TODAYS_FOLLOWUPS,
    TOOL_GET_OVERDUE_FOLLOWUPS,
    TOOL_GET_DASHBOARD_SUMMARY,
    TOOL_SEARCH_CUSTOMER,
    TOOL_SEARCH_LEAD,
    TOOL_CREATE_LEAD,
    TOOL_GET_CATALOG_ITEMS,
]

CUSTOMER_TOOLS = [
    TOOL_GET_CUSTOMER_OUTSTANDING,
    TOOL_LIST_CUSTOMER_INVOICES,
    TOOL_LIST_CUSTOMER_PAYMENTS,
    TOOL_SEARCH_LEAD,
    TOOL_CREATE_LEAD,
    TOOL_GET_CATALOG_ITEMS,
]

LEAD_TOOLS = [
    TOOL_GET_LEAD_DETAILS,
    TOOL_GET_LEAD_FOLLOWUPS,
    TOOL_UPDATE_LEAD_STAGE,
    TOOL_SCHEDULE_FOLLOWUP,
    TOOL_GET_CUSTOMER_OUTSTANDING,
    TOOL_GET_CATALOG_ITEMS,
]

GLOBAL_TOOLS = [
    TOOL_SEARCH_CUSTOMER,
    TOOL_SEARCH_LEAD,
    TOOL_GET_TODAYS_FOLLOWUPS,
    TOOL_GET_OVERDUE_FOLLOWUPS,
    TOOL_GET_DASHBOARD_SUMMARY,
    TOOL_CREATE_LEAD,
    TOOL_GET_CATALOG_ITEMS,
]


def get_tools_for_context(context_type: str) -> list[dict]:
    mapping = {
        "dashboard": DASHBOARD_TOOLS,
        "customer": CUSTOMER_TOOLS,
        "lead": LEAD_TOOLS,
        "global": GLOBAL_TOOLS,
    }
    return mapping.get(context_type, GLOBAL_TOOLS)
