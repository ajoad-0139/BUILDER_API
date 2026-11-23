from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from sites.utils.responses import success_response
from sites.services.preview_publish import PreviewPublishService


class PreviewSite (APIView) :

    permission_classes = [IsAuthenticated]
    
    services = PreviewPublishService

    def post(self, request, site_id) :

        self.services.preview_and_publish(is_publish=False, site_id=site_id, user=request.user)


class PublishSite (APIView) :

    permission_classes = [IsAuthenticated]
        
    services = PreviewPublishService

    def post(self, request, site_id) :

        self.services.preview_and_publish(is_publish=True)