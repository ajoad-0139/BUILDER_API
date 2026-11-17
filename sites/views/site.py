from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from sites.utils.pagination import DefaultPagination
from sites.utils.responses import success_response
from sites.services.site import SiteService, SiteImageService, SiteVideoService
from sites.utils.cache_core import SITES, cached, site_images, site_videos, site_detail, signature, user_sites
from sites.serializers.site import (SiteCreateInputSerializer, SiteListQuerySerializer, SiteOutputSerializer,
    SiteUpdateInputSerializer, SiteImageCreateInputSerializer, SiteImageListQuerySerializer, SiteImageOutputSerializer,
    SiteVideoCreateInputSerializer, SiteVideoListQuerySerializer, SiteVideoOutputSerializer, SiteImageUpdateInputSerializer,
    SiteVideoUpdateInputSerializer
    )


class SiteList(APIView):
    """Handle listing and creating sites for the authenticated user."""

    permission_classes = [IsAuthenticated]

    services = SiteService
    create_input_serializer = SiteCreateInputSerializer
    list_query_serializer = SiteListQuerySerializer
    output_serializer = SiteOutputSerializer
    pagination_class = DefaultPagination
    
    def get(self, request):
        """Retrieve the authenticated user's sites.

        Args:
            request: HTTP request containing query parameters.

        Returns:
            Response containing paginated sites.
        """

        def build() :
            query = self.list_query_serializer(data=request.query_params, partial=True)
            query.is_valid(raise_exception=True)

            sites = self.services.list(user=request.user, **query.validated_data)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(sites, request, view=self)
            items = self.output_serializer(page, many=True).data

            return paginator.get_paginated_data(items)

        data = cached(user_sites(request.user.pk), signature(request, "list"), build)

        return success_response(data, "Sites retrieved")
 
    def post(self, request):
        """Create a new site.

        Args:
            request: HTTP request containing site data.

        Returns:
            Response containing the created site.
        """


        serializer = self.create_input_serializer(data=request.data, context={"request": request},)
        serializer.is_valid(raise_exception=True)

        site = self.services.create(**serializer.validated_data)

        return success_response(
            self.output_serializer(site).data,
            "Site created",
            status.HTTP_201_CREATED,
        )
 

 
class SiteDetail(APIView):
    """Handle retrieving, updating, and deleting a site."""

    permission_classes = [IsAuthenticated]

    services = SiteService
    output_serializer = SiteOutputSerializer
    update_input_serializer = SiteUpdateInputSerializer
    
    def get(self, request, site_id):
        """Retrieve a site by its ID.

        Args:
            request: HTTP request.
            site_id: ID of the site.

        Returns:
            Response containing the site.
        """

        def build() :
            site = self.services.get(site_id=site_id)

            return self.output_serializer(site).data
        
        data = cached(site_detail(site_id), signature(request,"details", site_id), build)
        return success_response(data,"Site retrieved")
    
    def patch(self, request, site_id):
        """Partially update a site.

        Args:
            request: HTTP request containing fields to update.
            site_id: ID of the site.

        Returns:
            Response containing the updated site.
        """


        site = self.services.get(site_id=site_id)

        serializer = self.update_input_serializer( site, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)

        site = self.services.update(site=site, user=request.user, **serializer.validated_data)

        return success_response(
            self.output_serializer(site).data,
            "Site updated",
        )
 
    def delete(self, request, site_id):
        """Delete a site.

        Args:
            request: HTTP request.
            site_id: ID of the site.

        Returns:
            Response confirming deletion.
        """


        self.services.delete(site_id=site_id, user=request.user)
        return success_response(message="Site deleted")


class SiteImageList(APIView):
    """Handle listing and uploading images for a site."""

    permission_classes = [IsAuthenticated]

    services = SiteImageService
    create_input_serializer = SiteImageCreateInputSerializer
    list_query_serializer = SiteImageListQuerySerializer
    output_serializer = SiteImageOutputSerializer

    def get(self, request, site_id):
        """Retrieve images belonging to a site.

        Args:
            request: HTTP request containing query parameters.
            site_id: ID of the site.

        Returns:
            Response containing serialized images.
        """

        def build() :
            query = self.list_query_serializer(data=request.query_params)
            query.is_valid(raise_exception=True)

            images = self.services.list(site_id=site_id, **query.validated_data)

            return self.output_serializer(images, many=True, context={"request": request}).data

        data = cached(site_images(site_id), signature(request, "list", site_id), build)

        return success_response(data, "Images retrieved")

    def post(self, request, site_id):
        """Upload images to a site.

        Args:
            request: HTTP request containing image files and metadata.
            site_id: ID of the site.

        Returns:
            Response containing the uploaded images.
        """


        serializer = self.create_input_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        images = self.services.create( site_id=site_id, user=request.user, **serializer.validated_data)

        return success_response(
            self.output_serializer(images, many=True).data,
            "Images uploaded",
            status.HTTP_201_CREATED,
        )
 
class SiteImageDetail(APIView):
    """Handle retrieving, updating, and deleting a site image."""

    permission_classes = [IsAuthenticated]

    services = SiteImageService
    update_input_serializer = SiteImageUpdateInputSerializer
    output_serializer = SiteImageOutputSerializer
    
    def get(self, request, site_id, image_id):
        """Retrieve an image belonging to a site.

        Args:
            request: HTTP request.
            site_id: ID of the site.
            image_id: ID of the image.

        Returns:
            Response containing the image.
        """

        def build():
            image = self.services.get(site_id=site_id, image_id=image_id)
            return self.output_serializer(image, context={"request": request}).data
        
        data = cached(site_images(site_id), signature(request,"details",site_id), build)
        return success_response(data, "Image retrieved")
 
    def patch(self, request, site_id, image_id):
        """Partially update a site image.

        Args:
            request: HTTP request containing image fields to update.
            site_id: ID of the site.
            image_id: ID of the image.

        Returns:
            Response containing the updated image.
        """


        serializer = self.update_input_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        image = self.services.update(site_id=site_id, image_id=image_id, user=request.user, **serializer.validated_data)

        return success_response(
            self.output_serializer(image).data,
            "Image updated",
        )
 
    def delete(self, request, site_id, image_id):
        """Delete an image from a site.

        Args:
            request: HTTP request.
            site_id: ID of the site.
            image_id: ID of the image.

        Returns:
            Response confirming deletion.
        """


        self.services.delete(site_id=site_id, image_id=image_id, user=request.user)
        return success_response(message="Image deleted")
 
 
class SiteVideoList(APIView):
    """Handle listing and uploading videos for a site."""

    permission_classes = [IsAuthenticated]

    services = SiteVideoService
    create_input_serializer = SiteVideoCreateInputSerializer
    list_query_serializer = SiteVideoListQuerySerializer
    output_serializer = SiteVideoOutputSerializer
 
    def get(self, request, site_id):
        """Retrieve videos belonging to a site.

        Args:
            request: HTTP request containing query parameters.
            site_id: ID of the site.

        Returns:
            Response containing serialized videos.
        """


        def build() :
            query = self.list_query_serializer(data=request.query_params)
            query.is_valid(raise_exception=True)

            videos = self.services.list(site_id=site_id, **query.validated_data)

            return self.output_serializer(videos, many=True).data

        data = cached(site_videos(site_id), signature(request, "list", site_id), build)
        return success_response(data, "Videos retrieved")
 
    def post(self, request, site_id):
        """Upload videos to a site.

        Args:
            request: HTTP request containing video files and metadata.
            site_id: ID of the site.

        Returns:
            Response containing the uploaded videos.
        """


        serializer = self.create_input_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        videos = self.services.create(site_id=site_id, user=request.user, **serializer.validated_data)

        return success_response(
            self.output_serializer(videos, many=True, context={"request": request}).data,
            "Videos uploaded",
            status.HTTP_201_CREATED,
        )
 
 
class SiteVideoDetail(APIView):
    """Handle retrieving, updating, and deleting a site video."""

    permission_classes = [IsAuthenticated]

    services = SiteVideoService
    update_input_serializer = SiteVideoUpdateInputSerializer
    output_serializer = SiteVideoOutputSerializer
 
    def get(self, request, site_id, video_id):
        """Retrieve a video belonging to a site.

        Args:
            request: HTTP request.
            site_id: ID of the site.
            video_id: ID of the video.

        Returns:
            Response containing the video.
        """

        def build():
            video = self.services.get(site_id=site_id, video_id=video_id)
            return self.output_serializer(video).data
        
        data = cached(site_videos(site_id), signature(request, "details", site_id), build)
        return success_response(data, "Video retrieved")
 
    def patch(self, request, site_id, video_id):
        """Partially update a site video.

        Args:
            request: HTTP request containing video fields to update.
            site_id: ID of the site.
            video_id: ID of the video.

        Returns:
            Response containing the updated video.
        """


        serializer = self.update_input_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        video = self.services.update(site_id=site_id, video_id=video_id, user=request.user, **serializer.validated_data)

        return success_response(
            self.output_serializer(video).data,
            "Video updated",
        )
 
    def delete(self, request, site_id, video_id):
        """Delete a video from a site.

        Args:
            request: HTTP request.
            site_id: ID of the site.
            video_id: ID of the video.

        Returns:
            Response confirming deletion.
        """

        
        self.services.delete(site_id=site_id, video_id=video_id, user=request.user)
        return success_response(message="Video deleted")