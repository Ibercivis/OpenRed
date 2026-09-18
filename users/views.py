from django.contrib.auth.models import User
from django.db import transaction
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

    @me.mapping.delete
    def delete_me(self, request):
        """
        Delete the authenticated user's account.

        Requires re-authentication so a stolen token / hijacked session cannot
        wipe an account, and to guard against accidental deletion:
            - Password accounts: must send `current_password`, which is verified.
            - Social-login accounts (no usable password): must send `confirm: true`.

        Accepts a `delete_data` flag (request body or query param) to choose
        what happens to the data the user contributed:
            - False (default): the account is deleted but the user's
              measurements and tracks are kept and anonymized (their user/
              created_by FK is set to NULL via on_delete=SET_NULL). This
              preserves the scientific dataset while removing the personal link.
            - True: the user's measurements and tracks are also deleted.

        Returns:
            Response: 200 with a summary of what was removed/anonymized.
        """
        return self._delete_account(request)

    def destroy(self, request, *args, **kwargs):
        """
        Handle DELETE /api/users/{pk}/.

        Routed through the same confirmed deletion path as /api/users/me/ so the
        default ModelViewSet destroy cannot be used to bypass re-authentication.
        get_object() still enforces that users may only target their own account
        (404 otherwise).
        """
        self.get_object()
        return self._delete_account(request)

    def _delete_account(self, request):
        """
        Shared account-deletion logic with re-authentication and an optional
        data wipe. See delete_me() for the public contract.
        """
        from measures.models import (
            RadiationMeasurement,
            LightPollutionMeasurement,
            Track,
        )

        user = request.user

        # Re-authentication is the real security control here: a frontend
        # confirmation modal is only UX and can be bypassed by calling this
        # endpoint directly with a valid token.
        if user.has_usable_password():
            current_password = request.data.get('current_password', '')
            if not current_password or not user.check_password(current_password):
                return Response(
                    {'detail': 'Current password is incorrect or missing.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            # Social-login accounts have no usable password; require an explicit
            # confirmation flag instead.
            confirmed = str(request.data.get('confirm', '')).lower() in ('true', '1', 'yes', 'on')
            if not confirmed:
                return Response(
                    {'detail': 'Account has no password (social login). Send "confirm": true to delete.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        raw_flag = request.data.get('delete_data', request.query_params.get('delete_data', False))
        delete_data = str(raw_flag).lower() in ('true', '1', 'yes', 'on')

        radiation_count = RadiationMeasurement.objects.filter(user=user).count()
        light_count = LightPollutionMeasurement.objects.filter(user=user).count()
        track_count = Track.objects.filter(created_by=user).count()

        with transaction.atomic():
            if delete_data:
                # Deleting tracks cascades to their measurements and spectra;
                # the remaining measurements (not tied to a track) are removed next.
                Track.objects.filter(created_by=user).delete()
                RadiationMeasurement.objects.filter(user=user).delete()
                LightPollutionMeasurement.objects.filter(user=user).delete()
            # Deleting the user sets the remaining FKs (measurement.user,
            # track.created_by, device.owner, project/mission/campaign.created_by)
            # to NULL thanks to on_delete=SET_NULL.
            user.delete()

        action_taken = 'deleted' if delete_data else 'anonymized'
        return Response(
            {
                'detail': 'Account deleted successfully.',
                'data_deleted': delete_data,
                'measurements_%s' % action_taken: radiation_count + light_count,
                'tracks_%s' % action_taken: track_count,
            },
            status=status.HTTP_200_OK,
        )

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

