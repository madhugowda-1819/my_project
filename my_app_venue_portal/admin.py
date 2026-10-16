from django.contrib import admin

from my_app.models import Court, Venue, VenueSport


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'opening_time', 'closing_time', 'active', 'is_demo', 'source_ground')
    list_filter = ('active', 'is_demo', 'city')
    search_fields = ('name', 'city', 'address', 'source_ground__name')
    autocomplete_fields = ('source_ground',)


@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):
    list_display = ('name', 'venue', 'sport', 'capacity', 'price_per_hour', 'active')
    list_filter = ('active', 'sport')
    search_fields = ('name', 'venue__name')


@admin.register(VenueSport)
class VenueSportAdmin(admin.ModelAdmin):
    list_display = ('venue', 'sport')
