from django.shortcuts import render
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.paginator import Paginator
from django.db import connection
from .models import Project, RadiationMeasurement, LightPollutionMeasurement, Track
from .serializers import (
    ProjectSerializer, 
    RadiationMeasurementSerializer, 
    LightPollutionMeasurementSerializer, 
    TrackSerializer,
    MeasurementSerializer  # Alias de compatibilidad
)

# Create your views here.

class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    
    def get_queryset(self):
        """
        Optimizar queryset para incluir información relacionada
        """
        return Project.objects.all().order_by('name')
    
    def list(self, request, *args, **kwargs):
        """
        Override list para pre-calcular conteos de mediciones de manera eficiente
        """
        projects = self.get_queryset()
        
        # Pre-calcular conteos para todos los proyectos de una vez usando el ORM de Django
        for project in projects:
            if project.project_type == 'radiation':
                project._measurements_count = RadiationMeasurement.objects.filter(project=project).count()
                last_measurement = RadiationMeasurement.objects.filter(project=project).order_by('-dateTime').first()
                project._last_measurement_date = last_measurement.dateTime if last_measurement else None
            elif project.project_type == 'light_pollution':
                project._measurements_count = LightPollutionMeasurement.objects.filter(project=project).count()
                last_measurement = LightPollutionMeasurement.objects.filter(project=project).order_by('-dateTime').first()
                project._last_measurement_date = last_measurement.dateTime if last_measurement else None
            else:
                project._measurements_count = 0
                project._last_measurement_date = None
        
        # Usar el comportamiento estándar de DRF con los datos pre-calculados
        return super().list(request, *args, **kwargs)
    
    @action(detail=False, methods=['get'])
    def with_counts(self, request):
        """
        Endpoint específico para obtener proyectos con conteos de mediciones.
        Usa el mismo método optimizado que list().
        """
        return self.list(request)

class RadiationMeasurementViewSet(viewsets.ModelViewSet):
    queryset = RadiationMeasurement.objects.all()
    serializer_class = RadiationMeasurementSerializer

    @action(detail=False, methods=['get'])
    def count(self, request):
        """
        Endpoint para obtener el conteo total de mediciones de radiación
        """
        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()
        return Response({'total': total})

    @action(detail=False, methods=['get'])
    def paginated(self, request):
        """
        Endpoint para obtener mediciones de radiación paginadas con metadata de progreso
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

class LightPollutionMeasurementViewSet(viewsets.ModelViewSet):
    queryset = LightPollutionMeasurement.objects.all()
    serializer_class = LightPollutionMeasurementSerializer

    @action(detail=False, methods=['get'])
    def count(self, request):
        """
        Endpoint para obtener el conteo total de mediciones de contaminación lumínica
        """
        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()
        return Response({'total': total})

    @action(detail=False, methods=['get'])
    def paginated(self, request):
        """
        Endpoint para obtener mediciones de contaminación lumínica paginadas con metadata de progreso
        """
        page = request.GET.get('page', 1)
        page_size = request.GET.get('page_size', 1000)

        try:
            page = int(page)
            page_size = int(page_size)
            page_size = min(page_size, 15000)
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
            }, status=200)

        page_obj = paginator.get_page(page)
        serializer = self.get_serializer(page_obj, many=True)

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

class TrackViewSet(viewsets.ModelViewSet):
    queryset = Track.objects.all()
    serializer_class = TrackSerializer

# Para compatibilidad temporal con el frontend existente
# El frontend sigue llamando a /measurements/, por lo que mantenemos este ViewSet
class MeasurementViewSet(viewsets.ModelViewSet):
    # Por defecto, usa RadiationMeasurement para compatibilidad
    queryset = RadiationMeasurement.objects.all()
    serializer_class = MeasurementSerializer

    def get_queryset(self):
        """
        Filtrar mediciones por proyecto si se especifica el parámetro.
        Retorna el queryset apropiado según el tipo de proyecto.
        """
        project_id = self.request.query_params.get('project', None)
        
        if project_id:
            try:
                project_id = int(project_id)
                project = Project.objects.get(id=project_id)
                
                # Retornar queryset según el tipo de proyecto
                if project.project_type == 'radiation':
                    return RadiationMeasurement.objects.filter(project_id=project_id)
                elif project.project_type == 'light_pollution':
                    return LightPollutionMeasurement.objects.filter(project_id=project_id)
                
            except (ValueError, TypeError, Project.DoesNotExist):
                pass  # Ignorar valores inválidos, usar queryset por defecto
        
        # Por defecto, retornar RadiationMeasurement para compatibilidad
        return super().get_queryset()

    @action(detail=False, methods=['get'])
    def count(self, request):
        """
        Endpoint para obtener el conteo total de mediciones.
        Cuenta mediciones del tipo correcto según el proyecto especificado.
        """
        project_id = self.request.query_params.get('project', None)
        
        if project_id:
            try:
                project_id = int(project_id)
                project = Project.objects.get(id=project_id)
                
                # Contar según el tipo de proyecto
                if project.project_type == 'radiation':
                    total = RadiationMeasurement.objects.filter(project_id=project_id).count()
                elif project.project_type == 'light_pollution':
                    total = LightPollutionMeasurement.objects.filter(project_id=project_id).count()
                else:
                    total = 0
                
                return Response({
                    'total': total,
                    'project_id': project_id,
                    'project_type': project.project_type,
                    'project_name': project.name
                })
                
            except (ValueError, TypeError):
                return Response({'error': 'Invalid project_id parameter'}, status=400)
            except Project.DoesNotExist:
                return Response({'error': f'Project with id {project_id} does not exist'}, status=404)
        
        # Si no se especifica proyecto, contar solo RadiationMeasurement por compatibilidad
        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()
        return Response({'total': total, 'note': 'No project specified, counting radiation measurements only'})

    @action(detail=False, methods=['get'])
    def paginated(self, request):
        """
        Endpoint para obtener mediciones paginadas con metadata de progreso.
        Retorna mediciones del tipo correcto según el proyecto especificado.
        """
        page = request.GET.get('page', 1)
        page_size = request.GET.get('page_size', 1000)
        project_id = request.GET.get('project', None)

        try:
            page = int(page)
            page_size = int(page_size)
            # Aumentar el límite máximo para permitir chunks más grandes
            page_size = min(page_size, 15000)  # Límite máximo más alto
        except ValueError:
            return Response({'error': 'Invalid page or page_size parameter'}, status=400)

        # Determinar el queryset correcto según el proyecto
        if project_id:
            try:
                project_id = int(project_id)
                project = Project.objects.get(id=project_id)
                
                if project.project_type == 'radiation':
                    queryset = RadiationMeasurement.objects.filter(project_id=project_id)
                    serializer_class = RadiationMeasurementSerializer
                elif project.project_type == 'light_pollution':
                    queryset = LightPollutionMeasurement.objects.filter(project_id=project_id)
                    serializer_class = LightPollutionMeasurementSerializer
                else:
                    return Response({'error': f'Unsupported project type: {project.project_type}'}, status=400)
                
            except (ValueError, TypeError):
                return Response({'error': 'Invalid project_id parameter'}, status=400)
            except Project.DoesNotExist:
                return Response({'error': f'Project with id {project_id} does not exist'}, status=404)
        else:
            # Sin proyecto especificado, usar comportamiento por defecto
            queryset = self.filter_queryset(self.get_queryset())
            serializer_class = self.get_serializer_class()

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
                'project_id': project_id if project_id else None,
                'error': 'Page beyond available pages'
            }, status=200)  # Cambiar a 200 para mejor manejo

        page_obj = paginator.get_page(page)
        serializer = serializer_class(page_obj, many=True)

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
            'loaded_so_far': loaded_so_far,
            'project_id': project_id if project_id else None
        })