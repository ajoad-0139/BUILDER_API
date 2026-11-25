"""Define the default pagination configuration for API responses."""

from rest_framework.pagination import LimitOffsetPagination


class DefaultPagination(LimitOffsetPagination):
    """Provide limit/offset pagination: ?limit=20&offset=40."""

    default_limit = 10
    max_limit = 100
    limit_query_param = "limit"
    offset_query_param = "offset"

    def get_paginated_data(self, items):
        """Return paginated items with pagination metadata."""
        
        return {
            "items": items,
            "pagination": {
                "count": self.count,
                "limit": self.limit,
                "offset": self.offset,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
            },
        }