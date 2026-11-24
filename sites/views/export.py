from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from sites.utils.responses import success_response
from sites.services.export import ExportService


class PreviewSite(APIView):
    """Preview a site's exported content."""

    permission_classes = [IsAuthenticated]
    services = ExportService

    def post(self, request, site_id):
        """Generate a preview of the specified site.

        Args:
            request: The HTTP request containing the authenticated user.
            site_id: The ID of the site to preview.

        Returns:
            Response: A success response containing the preview result.
        """
        result = self.services.export(is_publish=False, site_id=site_id, user=request.user)
        return success_response(result, "site previewed", status.HTTP_201_CREATED)


class PublishSite(APIView):
    """Publish a site's exported content."""

    permission_classes = [IsAuthenticated]
    services = ExportService

    def post(self, request, site_id):
        """Publish the specified site.

        Args:
            request: The HTTP request containing the authenticated user.
            site_id: The ID of the site to publish.

        Returns:
            Response: A success response containing the publish result.
        """
        result = self.services.export(is_publish=True, site_id=site_id, user=request.user)
        return success_response(result, "site published", status.HTTP_201_CREATED)