from django.contrib.auth.models import User
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Count, Max, Min
from .serializers import UserSerializer


class UserViewSet(viewsets.ModelViewSet):
    """
    API ViewSet for user management.
    
    Permissions:
        - List/Retrieve: Authenticated users can view all users
        - Create: Not allowed (use registration endpoint)
        - Update/Delete: Only own account
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """
        Block access to other users' data.
        Users can only access their own account via /api/users/me/
        """
        queryset = super().get_queryset()
        
        # Only allow access to own account
        return queryset.filter(id=self.request.user.id)
    
    def list(self, request, *args, **kwargs):
        """
        Block listing all users. Use /api/users/me/ instead.
        """
        return Response(
            {'detail': 'Listing users is not allowed. Use /api/users/me/ to get your profile.'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    def retrieve(self, request, *args, **kwargs):
        """
        Block retrieving specific user by ID. Use /api/users/me/ instead.
        """
        # Only allow if requesting own user via special 'me' endpoint
        if kwargs.get('pk') == 'me':
            return super().retrieve(request, *args, **kwargs)
        
        return Response(
            {'detail': 'Access to user profiles is not allowed. Use /api/users/me/ to get your own profile.'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        """
        Get the authenticated user's profile.
        
        Returns:
            Response: JSON with user data (id, username, email, date_joined)
        """
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        """
        Get the authenticated user's profile.
        
        Returns:
            Response: JSON with user data (id, username, email, date_joined)
        """
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='me/stats')
    def my_stats(self, request):
        """
        Get statistics for the authenticated user.
        
        Returns:
            Response: JSON with user statistics including:
                - total_measurements: Total number of measurements taken
                - total_tracks: Total number of tracks uploaded
                - total_missions: Number of missions participated in
                - total_campaigns: Number of campaigns participated in
                - first_measurement_date: Date of first measurement
                - last_measurement_date: Date of last measurement
                - highest_dose_rate: Highest radiation dose rate recorded (μSv/h)
        """
        from measures.models import RadiationMeasurement, LightPollutionMeasurement, Track
        from missions.models import Mission, Campaign
        
        user = request.user
        
        # Count total measurements (radiation + light pollution)
        radiation_count = RadiationMeasurement.objects.filter(user=user).count()
        light_pollution_count = LightPollutionMeasurement.objects.filter(user=user).count()
        total_measurements = radiation_count + light_pollution_count
        
        # Count tracks created by user
        total_tracks = Track.objects.filter(created_by=user).count()
        
        # Count unique missions in user's tracks
        total_missions = Track.objects.filter(
            created_by=user,
            mission__isnull=False
        ).values('mission').distinct().count()
        
        # Count unique campaigns in user's tracks
        total_campaigns = Track.objects.filter(
            created_by=user,
            campaign__isnull=False
        ).values('campaign').distinct().count()
        
        # Get first and last measurement dates
        radiation_dates = RadiationMeasurement.objects.filter(user=user).aggregate(
            first=Min('dateTime'),
            last=Max('dateTime')
        )
        light_dates = LightPollutionMeasurement.objects.filter(user=user).aggregate(
            first=Min('dateTime'),
            last=Max('dateTime')
        )
        
        # Combine dates from both measurement types
        first_dates = [d for d in [radiation_dates['first'], light_dates['first']] if d]
        last_dates = [d for d in [radiation_dates['last'], light_dates['last']] if d]
        
        first_measurement_date = min(first_dates) if first_dates else None
        last_measurement_date = max(last_dates) if last_dates else None
        
        # Get highest dose rate
        highest_dose = RadiationMeasurement.objects.filter(
            user=user,
            dose_rate__isnull=False
        ).aggregate(max_dose=Max('dose_rate'))
        highest_dose_rate = highest_dose['max_dose']
        
        return Response({
            'total_measurements': total_measurements,
            'total_tracks': total_tracks,
            'total_missions': total_missions,
            'total_campaigns': total_campaigns,
            'first_measurement_date': first_measurement_date,
            'last_measurement_date': last_measurement_date,
            'highest_dose_rate': highest_dose_rate
        })

