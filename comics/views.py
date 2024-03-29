from django.http import Http404
from rest_framework import mixins, viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from comics.models import (Arc, Issue, Publisher, Series)
from comics.serializers import (ArcSerializer, ComicPageSerializer,
                                IssueSerializer, PublisherSerializer,
                                ReaderSerializer, SeriesSerializer)
from comics.tasks import import_comic_files_task
from comics.tasks import import_comic_files_novine_task

class ArcViewSet(viewsets.ReadOnlyModelViewSet):
    """
    list:
    Returns a list of all the story arcs.

    retrieve:
    Returns the information of an individual story arc.
    """
    queryset = (
        Arc.objects
        .prefetch_related('issue_set')
    )
    serializer_class = ArcSerializer
    filter_backends = (filters.SearchFilter,)
    search_fields = ('name',)
    lookup_field = 'slug'

    @action(detail=True)
    def issue_list(self, request, slug=None):
        """
        Returns a list of issues for a story arc.
        """
        arc = self.get_object()
        # Ordering the query set by date and then series name
        # since the Comic Vine api doesn't appear to provide
        # the story arc reading order.
        queryset = (
            arc.issue_set
            .select_related('series')
            .prefetch_related('credits_set', 'credits_set__creator', 'credits_set__role', 'arcs')
            .order_by('date', 'series', 'number')
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = IssueSerializer(
                page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)
        else:
            raise Http404()


class IssueViewSet(mixins.UpdateModelMixin,
                   mixins.ListModelMixin,
                   mixins.RetrieveModelMixin,
                   viewsets.GenericViewSet):
    """
    list:
    Returns a list of all issues.

    retrieve:
    Returns the information of an individual issue.

    update:
    Update the leaf and status for an issues.
    """
    queryset = (
        Issue.objects
        .select_related('series')
        .prefetch_related('credits_set', 'credits_set__creator', 'credits_set__role', 'arcs')
    )
    serializer_class = IssueSerializer
    lookup_field = 'slug'

    @action(detail=True, url_path='get-page/(?P<page>[0-9]+)')
    def get_page(self, request, slug=None, page=None):
        """
        Returns the base 64 image of the page from an issue.
        """
        issue = self.get_object()
        page_json = ComicPageSerializer(issue, many=False, context={
                                        'page_number': self.kwargs['page']})
        return Response(page_json.data)

    @action(detail=True)
    def reader(self, request, slug=None):
        """
        Returns information from the issue needed for the Thwip reader.
        """
        issue = self.get_object()
        page_json = ReaderSerializer(
            issue, many=False, context={"request": request})
        return Response(page_json.data)

    @action(detail=False, url_path='import-comics')
    def import_comics(self, request):
        """
        Updated the user's comic archive collection.
        """
        import_comic_files_task.apply_async()
        return Response(data={"import_comics": "Started imports."})

    @action(detail=False, url_path='import-comics-no-vine')
    def import_comics_no_vine(self, request):
        """
        Updated the user's comic archive collection.
        """
        import_comic_files_novine_task.apply_async()
        return Response(data={"import_comics_novine": "Started imports."})


    @action(detail=False)
    def recent(self, request):
        """
        Returns the last 90 comic archives imported.
        """
        queryset = (
            Issue.objects
            .select_related('series')
            .prefetch_related('credits_set', 'credits_set__creator', 'credits_set__role')
            .order_by('-import_date')[:90]
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = IssueSerializer(
                page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)
        else:
            raise Http404()


class PublisherViewSet(viewsets.ReadOnlyModelViewSet):
    """
    list:
    Returns a list of all publishers.

    retrieve:
    Returns the information of an individual publisher.
    """
    queryset = (
        Publisher.objects
        .prefetch_related('series_set')
    )
    serializer_class = PublisherSerializer
    lookup_field = 'slug'

    @action(detail=True)
    def series_list(self, request, slug=None):
        """
        Returns a list of series for a publisher.
        """
        publisher = self.get_object()
        queryset = (
            publisher.series_set
            .prefetch_related('issue_set')
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = SeriesSerializer(
                page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)
        else:
            raise Http404()


class SeriesViewSet(viewsets.ReadOnlyModelViewSet):
    """
    list:
    Returns a list of all the comic series.

    retrieve:
    Returns the information of an individual comic series.
    """
    queryset = (
        Series.objects
        .prefetch_related('issue_set')
    )
    serializer_class = SeriesSerializer
    filter_backends = (filters.SearchFilter,)
    search_fields = ('name',)
    lookup_field = 'slug'

    @action(detail=True)
    def issue_list(self, request, slug=None):
        """
        Returns a list of issues for a series.
        """
        series = self.get_object()
        queryset = (
            series.issue_set
            .prefetch_related('credits_set', 'credits_set__creator', 'credits_set__role', 'arcs')
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = IssueSerializer(
                page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)
        else:
            raise Http404()
