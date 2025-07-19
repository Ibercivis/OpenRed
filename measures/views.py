from django.shortcuts import render
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.paginator import Paginator
from .models import Measurement
from .serializers import MeasurementSerializer

# Create your views here.


class MeasurementViewSet(viewsets.ModelViewSet):
    queryset = Measurement.objects.all()
    serializer_class = MeasurementSerializer

    @action(detail=False, methods=['get'])
    def count(self, request):
        """
        Endpoint para obtener el conteo total de mediciones
        """
        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()
        return Response({'total': total})

    @action(detail=False, methods=['get'])
    def paginated(self, request):
        """
        Endpoint para obtener mediciones paginadas con metadata de progreso
        """
        page = request.GET.get('page', 1)
        page_size = request.GET.get('page_size', 1000)

        try:
            page = int(page)
            page_size = int(page_size)
            # Aumentar el límite máximo para permitir chunks más grandes
            page_size = min(page_size, 15000)  # Límite máximo más alto
        except ValueError:
            return Response({'error': 'Invalid page or page_size parameter'}, status=400)

        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()

        paginator = Paginator(queryset, page_size)

        if page > paginator.num_pages:
            return Response({
                'results': [],
                'count': total,
                'num_pages': paginator.num_pages,
                'current_page': page,
                'page_size': page_size,
                'has_next': False,
                'has_previous': page > 1,
                'loaded_so_far': total,
                'error': 'Page beyond available pages'
            }, status=200)  # Cambiar a 200 para mejor manejo

        page_obj = paginator.get_page(page)
        serializer = self.get_serializer(page_obj, many=True)

        # Calcular correctamente los elementos cargados hasta ahora
        items_in_current_page = len(serializer.data)
        loaded_so_far = ((page - 1) * page_size) + items_in_current_page

        return Response({
            'results': serializer.data,
            'count': total,
            'num_pages': paginator.num_pages,
            'current_page': page,
            'page_size': page_size,
            'items_in_page': items_in_current_page,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
            'loaded_so_far': loaded_so_far
        })