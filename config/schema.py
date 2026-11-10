from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiParameter


class SmileSchema(AutoSchema):
    """Shows the simple ?filters of a viewset (its `filter_params`) in Swagger on list calls."""

    def get_override_parameters(self):
        params = super().get_override_parameters()
        if getattr(self.view, "action", None) == "list":
            for name in getattr(self.view, "filter_params", {}):
                params.append(OpenApiParameter(name, str, OpenApiParameter.QUERY, required=False))
        return params
