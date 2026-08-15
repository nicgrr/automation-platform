# Phase 1A tool policy

| Tool | Capability | Initial policy |
|---|---|---|
| `get_grocery_items` | Read grocery HTTP data | Denied until explicitly granted |
| `get_container_status` | Read allowlisted container state | Denied until explicitly granted |
| `get_container_logs` | Read at most 500 log lines | Denied until explicitly granted |
| `get_ebay_listings` | Read sandbox inventory | Denied until sandbox credential configured and granted |
| `search_ebay_market` | Search sandbox marketplace | Denied until sandbox credential configured and granted |
| `recommend_ebay_price` | Produce recommendation only | Denied until explicitly granted |

Restart, exec, listing mutation, shell, arbitrary HTTP, arbitrary filesystem, and
generic Docker tools are outside the Phase 1A tool registry.

