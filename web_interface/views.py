import logging
from decimal import Decimal, InvalidOperation
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.http import JsonResponse, Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode, url_has_allowed_host_and_scheme
from django.core.cache import cache
from .models import Location, Destination, PlaceToEat, RestaurantRating
from .forms import DestinationSubmissionForm, PlaceToEatSubmissionForm
from accounts.models import RecentView, SavedItem, UserPreference
from math import radians, sin, cos, sqrt, atan2
from datetime import datetime

logger = logging.getLogger(__name__)

# Configuration
EARTH_RADIUS_KM = 6371  # Earth's radius in kilometers
# No hard distance cutoff by default -- TravelEase covers all of Nepal, not
# just a 50km bubble around wherever the visitor happens to be (like Uber/
# InDrive, you should be able to search/book around a *different* place
# than your current location, e.g. for someone else or a future trip).
# RADIUS_CHOICES defines the *optional* filter presets shown in the UI;
# None means "no limit, show everything, nearest first".
RADIUS_CHOICES = [
    (None, 'Any distance'),
    (25, 'Within 25 km'),
    (100, 'Within 100 km'),
    (300, 'Within 300 km'),
    (800, 'All of Nepal'),  # Nepal is ~800km east-west
]

# Same cache.add()+cache.incr() atomic pattern as LOGIN_RATE_LIMIT_* in
# accounts/views.py -- keyed per-user (not per-IP) since submitting is
# already gated behind login_required, so there's always a real user id
# to key on, and one person's submissions shouldn't affect anyone sharing
# their network. Loose enough for a genuine visitor adding a handful of
# real places, tight enough to stop one account from flooding pending
# review with dozens of junk entries.
SUBMISSION_RATE_LIMIT_MAX = 5
SUBMISSION_RATE_LIMIT_WINDOW_SECONDS = 60 * 60

def get_user_location():
    """Default search origin: Kathmandu Valley.

    This app is scoped to Nepal only, and IP-based geolocation is not
    reliable enough to trust as a default -- on a home network, VPN, or
    shared/NAT'd connection it can resolve to a location hundreds or
    thousands of km away from the user's real position (this was
    previously happening here, producing nonsensical "11,193 km away"
    style distances on every result). Since every real destination is
    somewhere in Nepal anyway, centering the default view on Kathmandu
    Valley is a safe, always-correct starting point. Anyone who wants
    their actual position can opt in explicitly via the "Use My Location"
    GPS button (real browser geolocation, not IP guessing) or the
    location picker.
    """
    return {'latitude': 27.6660, 'longitude': 85.3227}


def resolve_search_origin(request):
    """
    Work out which lat/lon the search should be centered on, in priority
    order:
      1. Explicit ?latitude=&longitude= (e.g. from a "use my precise
         location" browser geolocation button)
      2. ?location_id=<Location.pk> -- set by the location picker dropdown
         on the search page, letting you browse around any saved place in
         Nepal (e.g. Pokhara) regardless of where you actually are.
      3. Hardcoded fallback (Kathmandu Valley) -- see get_user_location()
         for why this app doesn't attempt IP-based geolocation.

    Always returns (latitude, longitude, selected_location_or_None).
    Never raises -- bad/missing input just falls through to the next
    option, so this stays safe even against malformed real-world data.
    """
    # 1. Explicit coordinates
    try:
        lat_param = request.GET.get('latitude')
        lon_param = request.GET.get('longitude')
        if lat_param and lon_param:
            return float(lat_param), float(lon_param), None
    except (TypeError, ValueError):
        pass

    # 2. Named location picker
    location_id = request.GET.get('location_id')
    if location_id:
        try:
            loc = Location.objects.get(id=location_id)
            return float(loc.latitude), float(loc.longitude), loc
        except (Location.DoesNotExist, ValueError, TypeError):
            pass

    # 3. Hardcoded fallback (Kathmandu Valley)
    fallback = get_user_location()
    return fallback['latitude'], fallback['longitude'], None


def resolve_radius_km(request):
    """Optional distance filter, shared by every view that needs to parse
    ?radius=. Defaults to "no limit" -- results are always ranked
    nearest-first, but nothing is hidden just because it's far away.
    Never raises -- bad/missing input just means no limit is applied.

    Factored out so home() and search_travel_options() parse this
    identically -- having each view do its own inline parsing is exactly
    how the two pages' forms drifted out of sync with each other before
    (see home.html/search_results.html's shared history in git blame:
    'category' vs 'type', etc.) -- one implementation, used everywhere
    that needs it, can't drift.
    """
    radius_param = request.GET.get('radius', '').strip()
    if not radius_param:
        return None
    try:
        radius_km = float(radius_param)
    except (TypeError, ValueError):
        return None
    return radius_km if radius_km > 0 else None


# Same threshold as location_map.html's GPS_ACCURACY_THRESHOLD_M -- kept in
# sync deliberately so "is this fix good enough to trust" means the same
# thing everywhere in the app, not a stricter/looser bar depending on which
# page happens to be asking.
GPS_ACCURACY_THRESHOLD_M = 1500


def resolve_location_accuracy(request):
    """Parse the optional ?location_accuracy= (meters, from
    position.coords.accuracy) carried alongside ?latitude=&longitude=.
    Returns (accuracy_m_or_None, is_approximate). is_approximate is True
    only when we have a numeric accuracy AND it's worse than the GPS-grade
    threshold -- i.e. "we have a fix, but you should know it might be
    rough", not "we have no fix at all" (which is just accuracy_m=None).
    """
    raw = request.GET.get('location_accuracy', '').strip()
    if not raw:
        return None, False
    try:
        accuracy_m = float(raw)
    except (TypeError, ValueError):
        return None, False
    return accuracy_m, accuracy_m > GPS_ACCURACY_THRESHOLD_M

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points using Haversine formula.
    Returns float('inf') for any invalid/missing input instead of raising,
    so a single bad coordinate in the dataset can't crash a search."""
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
        
        lat1_rad = radians(lat1)
        lon1_rad = radians(lon1)
        lat2_rad = radians(lat2)
        lon2_rad = radians(lon2)
        
        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad
        
        a = sin(dlat/2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        distance = EARTH_RADIUS_KM * c
        
        return distance
    except (ValueError, TypeError):
        return float('inf')

def home(request):
    """Display homepage with featured restaurants and destinations, plus
    the hero search form.

    FIXED (found during audit): this form used to POST here with its own
    field vocabulary (`category` instead of `type`, no radius/location_id
    controls at all), and this view translated it into the query
    parameters search_travel_options actually expects before redirecting.
    That translation layer was a real, live source of bugs -- it's
    exactly how the two forms drifted out of sync (mismatched field
    names, home missing filters explore had, one silently discarding a
    filter the other preserved). The hero form now submits a plain GET
    directly to search_travel_options, using the exact same field names,
    same option values, and same JS behavior as that page's own filter
    form (see search_results.html) -- there is no longer a second
    implementation of "how search parameters work" to keep in sync.
    """
    location_id = request.GET.get('location_id')
    selected_location = None
    if location_id:
        try:
            selected_location = Location.objects.get(id=location_id)
        except (Location.DoesNotExist, ValueError, TypeError):
            selected_location = None

    # FOUND DURING AUDIT: neither Destination nor PlaceToEat defines a
    # Meta.ordering, so `.all()[:6]` with no .order_by() at all relied
    # entirely on the database's own undefined row order to decide which
    # "first 6" show up here -- SQLite happens to return insertion order
    # today, but Django's docs are explicit that this isn't guaranteed to
    # stay that way. order_by('id') pins it to the same insertion-order
    # behavior already documented in the README ("first 6 rows in the
    # database"), just actually guaranteed rather than incidental.
    restaurants = PlaceToEat.objects.filter(status='approved').select_related('location').order_by('id')[:6]
    trending_destinations = Destination.objects.filter(status='approved').select_related('location').order_by('id')[:6]

    return render(request, 'web_interface/home.html', {
        'restaurants': restaurants,
        'trending_destinations': trending_destinations,
        'search_term': request.GET.get('search', ''),
        'result_type': request.GET.get('type', ''),
        'all_locations': Location.objects.order_by('name'),
        'radius_choices': RADIUS_CHOICES,
        'radius_km': resolve_radius_km(request),
        'selected_location': selected_location,
    })

def places_to_eat(request):
    """Redirects to the unified search page, filtered to restaurants.

    This used to render its own separate template with its own query
    (a hard-coded top-10 PlaceToEat list, no pagination, and none of
    search_travel_options' distance/radius/rating/open-now filtering) --
    a second implementation of exactly the same "browse restaurants"
    feature, which could only ever drift further out of sync with the
    real one over time. Its own search form already just redirected to
    search_travel_options?type=place_to_eat on POST; GET now does the
    same, so there's a single feature-complete implementation of restaurant
    search/browsing rather than two.
    """
    base_url = reverse('web_interface:search_travel_options')
    if request.method == 'POST':
        search_term = request.POST.get('search', '').strip()
        if search_term:
            return redirect(f'{base_url}?{urlencode({"search": search_term, "type": "place_to_eat"})}')
    return redirect(f'{base_url}?{urlencode({"type": "place_to_eat"})}')

def search_travel_options(request):
    """Search for travel options and places to eat based on location and query."""
    
    # Get search parameters
    search_term = request.GET.get('search', '').strip()
    # A <select> always submits its currently-selected value, even the
    # "All Types" option's value="" -- there's no way for a <select> to
    # omit itself from form data the way an unchecked checkbox can. So
    # request.GET.get('type', 'all') was returning '' (not the 'all'
    # default) on essentially every plain search where the type filter
    # was left at its default, since 'type' *is* present in the
    # querystring, just empty. '' doesn't match either
    # `in ['all', 'destination']` or `in ['all', 'place_to_eat']' check
    # below, so BOTH branches were skipped -- silently zero results on
    # what should have been the most common case of all: a plain search
    # with no type filter applied. `or 'all'` catches both "key absent"
    # and "key present but empty" the same way.
    result_type = request.GET.get('type', '').strip() or 'all'  # 'all', 'place_to_eat', 'destination'

    # Restaurant-only filters from the hero search form. These only make
    # sense for restaurants, so they never affect destination results.
    open_now_only = request.GET.get('open_now') == 'true'
    try:
        min_rating = float(request.GET.get('min_rating', '0'))
    except (TypeError, ValueError):
        min_rating = 0

    # Where are we searching *around*? This is NOT necessarily "where the
    # visitor currently is" -- see resolve_search_origin() docstring. This
    # is what makes it possible to search/browse a different city than the
    # one you're physically in right now, similar to setting a pickup
    # location for someone else in a ride-hailing app.
    latitude, longitude, selected_location = resolve_search_origin(request)
    location_accuracy_m, location_is_approximate = resolve_location_accuracy(request)

    # Optional distance filter. Defaults to "no limit" -- results are
    # always ranked nearest-first, but nothing is hidden just because it's
    # far away. A user can optionally narrow with ?radius=25 etc.
    radius_km = resolve_radius_km(request)

    logger.debug(
        "Search request: term=%r type=%r lat=%s lon=%s radius=%s",
        search_term, result_type, latitude, longitude, radius_km,
    )

    # Initialize results
    results = []
    destinations = []
    places_to_eat = []
    
    # Build search queries
    if search_term:
        # Search destinations
        dest_query = Q(name__icontains=search_term) | Q(description__icontains=search_term) | Q(location__name__icontains=search_term)
        destinations = Destination.objects.filter(dest_query, status='approved').select_related('location')
        
        # Search restaurants
        rest_query = Q(name__icontains=search_term) | Q(cuisine__icontains=search_term) | Q(location__name__icontains=search_term)
        places_to_eat = PlaceToEat.objects.filter(rest_query, status='approved').select_related('location')
        
        logger.debug(
            "Search matched %d destinations, %d restaurants",
            destinations.count(), places_to_eat.count(),
        )
    else:
        # No search term - show all (approved) results. A pending/rejected
        # user submission is deliberately excluded here -- it's not public
        # until a staff member approves it (see submit_place's docstring
        # for the full moderation flow); its own detail page still works
        # for the submitter/staff via travel_detail/restaurant_detail.
        destinations = Destination.objects.filter(status='approved').select_related('location')
        places_to_eat = PlaceToEat.objects.filter(status='approved').select_related('location')
        logger.debug(
            "No search term; showing all %d destinations, %d restaurants",
            destinations.count(), places_to_eat.count(),
        )

    # Which of these results has the current user already saved? (used to
    # show correct save/unsave state on each result card)
    saved_keys = set()
    if request.user.is_authenticated:
        saved_keys = set(
            SavedItem.objects.filter(user=request.user).values_list('item_type', 'item_id')
        )

    # Process destinations
    if result_type in ['all', 'destination']:
        for destination in destinations:
            distance = None
            loc = destination.location
            # Use the destination's own coordinate when it has one (set by
            # `backfill_coordinates`), falling back to the parent Location's
            # centroid only when it doesn't -- same fallback every detail
            # page already uses via effective_latitude/effective_longitude.
            # Using loc.latitude/longitude unconditionally here would silently
            # diverge from the detail page's own map/directions the moment
            # any destination gets its own coordinate.
            dest_lat = destination.effective_latitude
            dest_lng = destination.effective_longitude
            if dest_lat is not None and dest_lng is not None:
                distance = calculate_distance(latitude, longitude, dest_lat, dest_lng)
                if distance == float('inf'):
                    distance = None

            # Only apply the radius filter when the user actually chose one
            # AND we could compute a real distance. Items with unknown/bad
            # coordinates are always included (with distance shown as
            # "unknown") rather than silently dropped -- this matters once
            # real-world data (which is never perfectly clean) replaces the
            # seed data.
            if radius_km is not None and distance is not None and distance > radius_km:
                continue

            results.append({
                'item': destination,
                'type': 'destination',
                'distance': round(distance, 2) if distance is not None else None,
                'location_name': loc.name if loc else 'Unknown location',
                'is_saved': ('destination', destination.id) in saved_keys,
            })

    # Process restaurants
    if result_type in ['all', 'place_to_eat']:
        for restaurant in places_to_eat:
            distance = None
            loc = restaurant.location
            # Same fix as destinations above -- own coordinate first, parent
            # Location centroid only as a fallback.
            rest_lat = restaurant.effective_latitude
            rest_lng = restaurant.effective_longitude
            if rest_lat is not None and rest_lng is not None:
                distance = calculate_distance(latitude, longitude, rest_lat, rest_lng)
                if distance == float('inf'):
                    distance = None

            if radius_km is not None and distance is not None and distance > radius_km:
                continue

            restaurant_is_open = is_open(restaurant.business_hours) if restaurant.business_hours else None
            # "Open now" only makes sense if we actually know the hours --
            # a restaurant with no listed hours can't be confirmed open,
            # so it's excluded rather than guessed at.
            if open_now_only and not restaurant_is_open:
                continue
            if min_rating > 0 and (restaurant.rating is None or restaurant.rating < Decimal(str(min_rating))):
                continue

            results.append({
                'item': restaurant,
                'type': 'place_to_eat',
                'distance': round(distance, 2) if distance is not None else None,
                'location_name': loc.name if loc else 'Unknown location',
                'is_open': restaurant_is_open,
                'is_saved': ('restaurant', restaurant.id) in saved_keys,
            })
    
    # Sort results by distance (closest first, unknown-distance items last), then by name
    results.sort(key=lambda x: (x['distance'] if x['distance'] is not None else float('inf'), x['item'].name))
    
    logger.debug("Final search results count: %d", len(results))

    # Pagination
    paginator = Paginator(results, 12)  # 12 items per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Pagination links need to preserve every other filter (search, type,
    # location_id, radius, etc). Build that querystring properly encoded
    # here rather than having the template reconstruct it by directly
    # interpolating request.GET values -- that approach breaks the exact
    # same way the earlier redirect bug did whenever a value contains '&'.
    preserved_params = request.GET.copy()
    preserved_params.pop('page', None)
    base_querystring = preserved_params.urlencode()

    querystring_without_type = request.GET.copy()
    querystring_without_type.pop('type', None)
    # Also drop 'page' -- removing the type filter changes the result set,
    # so whatever page number applied to the old (filtered) results may
    # not mean the same thing (or exist at all) in the new one. Paginator
    # clamps an out-of-range page rather than erroring, but landing on
    # "page 3" of a totally different result set is confusing regardless.
    querystring_without_type.pop('page', None)
    querystring_without_type = querystring_without_type.urlencode()

    context = {
        'results': page_obj,  # This is the paginated results
        'all_results': results,  # This is the full results list for counting
        'search_term': search_term,
        'search_query': search_term,  # Template expects this name
        'result_type': result_type,
        'total_results': len(results),
        'user_latitude': latitude,
        'user_longitude': longitude,
        'latitude': latitude,  # Template expects this
        'longitude': longitude,  # Template expects this
        'selected_location': selected_location,
        'all_locations': Location.objects.order_by('name'),
        'radius_km': radius_km,
        'radius_choices': RADIUS_CHOICES,
        'base_querystring': base_querystring,
        'querystring_without_type': querystring_without_type,
        'location_accuracy_m': location_accuracy_m,
        'location_is_approximate': location_is_approximate,
    }
    
    return render(request, 'web_interface/search_results.html', context)


def is_open(business_hours, current_time=None):
    """Check if a place is open based on business hours.
    Handles hours that cross midnight (e.g. '22:00-02:00' for a bar open
    10pm to 2am) -- for those, "open" means the current time is at or
    after the start OR at or before the end, not between them."""
    if not business_hours:
        return None
        
    if current_time is None:
        current_time = timezone.localtime(timezone.now()).time()
    # business_hours is only ever specified to the minute ('%H:%M' below),
    # but current_time (when it defaults to "now") carries seconds and
    # microseconds. Comparing full precision against a minute-only
    # boundary made a place falsely read as closed for up to 59 seconds
    # at the end of its listed closing minute -- e.g. '00:00-23:59'
    # (commonly used to mean "open all day") reported closed for the
    # entire last minute of the day except its very first second, since
    # 23:59:01-23:59:59.999999 all compare greater than the parsed
    # end_time of exactly 23:59:00. Truncating to minute precision here
    # makes the comparison consistent with the granularity the hours are
    # actually stored at.
    current_time = current_time.replace(second=0, microsecond=0)

    try:
        if '-' in business_hours:
            start_str, end_str = business_hours.split('-', 1)
            start_time = datetime.strptime(start_str.strip(), '%H:%M').time()
            end_time = datetime.strptime(end_str.strip(), '%H:%M').time()
            if start_time <= end_time:
                # Normal same-day hours, e.g. 09:00-21:00
                return start_time <= current_time <= end_time
            else:
                # Crosses midnight, e.g. 22:00-02:00
                return current_time >= start_time or current_time <= end_time
    except (ValueError, AttributeError) as e:
        logger.warning("Error parsing business hours %r: %s", business_hours, e)
        return None
    
    return None

@login_required
def saved_items(request):
    """Display user's saved items."""
    items = SavedItem.objects.filter(user=request.user).order_by('-saved_at')
    return render(request, 'web_interface/saved_items.html', {
        'saved_items': items,
        'destination_count': items.filter(item_type='destination').count(),
        'restaurant_count': items.filter(item_type='restaurant').count(),
    })

@login_required
def recent_views(request):
    """Display user's recent views."""
    views_qs = list(RecentView.objects.filter(user=request.user).order_by('-viewed_at'))

    # The template shows a save/unsave heart per item -- it needs to know
    # which of these are *already* saved, otherwise it always renders as
    # "not saved yet" even for items the user saved earlier. Since the
    # button posts to the same toggle_save_item endpoint used everywhere
    # else, clicking it on an already-saved item would then silently
    # un-save it with no warning (the same bug class as the recent-login
    # data that was fetched but never shown -- here the state is fetched
    # nowhere at all, so the button lies about what it's about to do).
    saved_keys = set(
        SavedItem.objects.filter(user=request.user).values_list('item_type', 'item_id')
    )
    for view in views_qs:
        view.is_saved = (view.item_type, view.item_id) in saved_keys

    return render(request, 'web_interface/recent_views.html', {'recent_views': views_qs})

def travel_detail(request, id):
    """Display details of a travel destination."""
    item = get_object_or_404(Destination, id=id)

    if item.status != 'approved':
        is_owner_or_staff = request.user.is_authenticated and (
            request.user.is_staff or item.submitted_by_id == request.user.id
        )
        if not is_owner_or_staff:
            raise Http404("Destination not found.")

    is_saved = False
    # Log recent view if user is authenticated
    if request.user.is_authenticated:
        RecentView.objects.update_or_create(
            user=request.user,
            item_type='destination',
            item_id=item.id,
            defaults={
                'item_title': item.name,
                'item_url': reverse('web_interface:travel_detail', args=[item.id]),
                'viewed_at': timezone.now(),
            }
        )
        is_saved = SavedItem.objects.filter(user=request.user, item_type='destination', item_id=item.id).exists()

    return render(request, 'web_interface/travel_detail.html', {'item': item, 'is_saved': is_saved})

def restaurant_detail(request, id):
    """Display details of a restaurant with open status."""
    restaurant = get_object_or_404(PlaceToEat, id=id)

    if restaurant.status != 'approved':
        is_owner_or_staff = request.user.is_authenticated and (
            request.user.is_staff or restaurant.submitted_by_id == request.user.id
        )
        if not is_owner_or_staff:
            raise Http404("Restaurant not found.")

    is_currently_open = is_open(restaurant.business_hours)

    is_saved = False
    user_rating = None
    # Log recent view if user is authenticated
    if request.user.is_authenticated:
        RecentView.objects.update_or_create(
            user=request.user,
            item_type='restaurant',
            item_id=restaurant.id,
            defaults={
                'item_title': restaurant.name,
                'item_url': reverse('web_interface:restaurant_detail', args=[restaurant.id]),
                'viewed_at': timezone.now(),
            }
        )
        is_saved = SavedItem.objects.filter(user=request.user, item_type='restaurant', item_id=restaurant.id).exists()
        user_rating = RestaurantRating.objects.filter(place=restaurant, user=request.user).first()

    return render(request, 'web_interface/restaurant_detail.html', {
        'restaurant': restaurant,
        'is_open': is_currently_open,
        'is_saved': is_saved,
        'user_rating': user_rating,
        'ratings_count': restaurant.user_ratings.count(),
    })


@login_required
def submit_place(request):
    """Let a logged-in user add a new destination or restaurant. Always
    created with status='pending' (see Destination.status/PlaceToEat.status
    help text) -- never immediately public. The submitter can still see
    their own submission's detail page and its status via 'My submissions'
    below; everyone else can't see it until a staff member approves it in
    the admin (see DestinationAdmin/PlaceToEatAdmin's approve/reject
    actions)."""
    place_type = request.POST.get('place_type') or request.GET.get('type') or 'destination'
    if place_type not in ('destination', 'place_to_eat'):
        place_type = 'destination'

    if request.method == 'POST':
        rate_limit_key = f'submit_place:{request.user.id}'
        recent_submissions = cache.get(rate_limit_key, 0)
        if recent_submissions >= SUBMISSION_RATE_LIMIT_MAX:
            messages.error(
                request,
                "You've submitted several places recently -- please wait a "
                "bit before adding more."
            )
            dest_form = DestinationSubmissionForm()
            rest_form = PlaceToEatSubmissionForm()
        else:
            FormClass = DestinationSubmissionForm if place_type == 'destination' else PlaceToEatSubmissionForm
            form = FormClass(request.POST)
            if form.is_valid():
                place = form.save(commit=False)
                place.submitted_by = request.user
                place.status = 'pending'
                place.save()

                # Atomic increment -- see LOGIN_RATE_LIMIT's comment in
                # accounts/views.py for why cache.add()+cache.incr() (not a
                # read-then-write of recent_submissions) is what actually
                # makes this race-free under concurrent submissions.
                cache.add(rate_limit_key, 0, SUBMISSION_RATE_LIMIT_WINDOW_SECONDS)
                cache.incr(rate_limit_key)

                messages.success(
                    request,
                    f'"{place.name}" has been submitted and is pending review. '
                    "You'll see it below once it's approved."
                )
                return redirect('web_interface:submit_place')

            # Invalid submission: re-render with errors on the form that
            # was actually submitted, and a fresh copy of the other one
            # (so switching tabs client-side still has a usable form).
            if place_type == 'destination':
                dest_form, rest_form = form, PlaceToEatSubmissionForm()
            else:
                dest_form, rest_form = DestinationSubmissionForm(), form
    else:
        dest_form = DestinationSubmissionForm()
        rest_form = PlaceToEatSubmissionForm()

    my_destinations = Destination.objects.filter(submitted_by=request.user).select_related('location').order_by('-id')
    my_restaurants = PlaceToEat.objects.filter(submitted_by=request.user).select_related('location').order_by('-id')

    return render(request, 'web_interface/submit_place.html', {
        'place_type': place_type,
        'dest_form': dest_form,
        'rest_form': rest_form,
        'my_destinations': my_destinations,
        'my_restaurants': my_restaurants,
    })


@login_required
@require_POST
def submit_restaurant_rating(request, id):
    """Let a signed-in user submit (or update) their rating and optional
    cost estimate for a restaurant. This is what fills in rating/
    average_cost for restaurants sourced from map data (e.g. OpenStreetMap)
    that doesn't include commercial info like this."""
    restaurant = get_object_or_404(PlaceToEat, id=id)
    redirect_url = reverse('web_interface:restaurant_detail', args=[restaurant.id])

    rating_raw = (request.POST.get('rating') or '').strip()
    cost_raw = (request.POST.get('average_cost') or '').strip()

    try:
        rating = Decimal(rating_raw)
    except InvalidOperation:
        messages.error(request, 'Please provide a valid rating between 0 and 5.')
        return redirect(redirect_url)
    if rating < 0 or rating > 5:
        messages.error(request, 'Rating must be between 0 and 5.')
        return redirect(redirect_url)

    average_cost = None
    if cost_raw:
        try:
            average_cost = Decimal(cost_raw)
        except InvalidOperation:
            messages.error(request, 'Average cost must be a valid number.')
            return redirect(redirect_url)
        if average_cost < 0:
            messages.error(request, 'Average cost cannot be negative.')
            return redirect(redirect_url)

    RestaurantRating.objects.update_or_create(
        place=restaurant, user=request.user,
        defaults={'rating': rating, 'average_cost': average_cost},
    )
    restaurant.recompute_aggregates()

    messages.success(request, 'Thanks! Your rating has been recorded.')
    return redirect(redirect_url)


@login_required
@require_POST
def toggle_save_item(request):
    """Save or unsave an item (destination or restaurant) for the current user.

    Accepts POST params: item_type ('destination'|'restaurant'), item_id.
    Works both as a normal form POST (redirects back) and as AJAX (returns JSON).
    """
    item_type = request.POST.get('item_type')
    item_id = request.POST.get('item_id')

    if item_type not in ('destination', 'restaurant') or not item_id:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Invalid item.'}, status=400)
        messages.error(request, 'Could not update saved items: invalid item.')
        return redirect('web_interface:home')

    # Check for an existing save FIRST. Removing one must always work, even
    # if the underlying destination/restaurant has since been deleted (e.g.
    # by an admin) -- otherwise anyone who'd saved it would be permanently
    # stuck with a dead entry they can never remove, since the object
    # lookup below would 404 before ever reaching the removal.
    existing = SavedItem.objects.filter(user=request.user, item_type=item_type, item_id=item_id).first()

    if existing:
        existing.delete()
        saved = False
        item_name = existing.item_title or 'Item'
    else:
        # Creating a new save does need the live object, to record its
        # current name/URL.
        if item_type == 'destination':
            obj = get_object_or_404(Destination, id=item_id)
            detail_url = reverse('web_interface:travel_detail', args=[obj.id])
        else:
            obj = get_object_or_404(PlaceToEat, id=item_id)
            detail_url = reverse('web_interface:restaurant_detail', args=[obj.id])

        SavedItem.objects.create(
            user=request.user,
            item_type=item_type,
            item_id=obj.id,
            item_title=obj.name,
            item_url=detail_url,
            # Snapshot at save time, same as item_title/item_url above --
            # not a live link to the destination/restaurant. See the
            # FOUND-DURING-AUDIT note on SavedItem.item_image for why this
            # was never actually being set before (the field existed but
            # nothing wrote to it, so saved_items.html had no image to
            # show and never even tried).
            item_image=obj.image,
        )
        saved = True
        item_name = obj.name

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'saved': saved})

    messages.success(request, f'{item_name} {"saved" if saved else "removed from saved items"}.')
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect('web_interface:saved_items')


@login_required
@require_POST
def remove_recent_view(request):
    """Remove a single entry from the current user's recent-views history."""
    view_id = request.POST.get('id')
    RecentView.objects.filter(user=request.user, id=view_id).delete()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True})
    messages.success(request, 'Removed from recent views.')
    return redirect('web_interface:recent_views')


@login_required
@require_POST
def clear_recent_views(request):
    """Clear all of the current user's recent-views history."""
    RecentView.objects.filter(user=request.user).delete()
    messages.success(request, 'Your recent views have been cleared.')
    return redirect('web_interface:recent_views')


def terms_of_service(request):
    return render(request, 'web_interface/terms.html')


def privacy_policy(request):
    return render(request, 'web_interface/privacy.html')


def contact(request):
    return render(request, 'web_interface/contact.html')


@require_POST
def newsletter_subscribe(request):
    """Handle the footer's 'Stay Updated' form.

    There's no separate email-marketing/subscriber system in this app --
    the only place a "subscribed to marketing emails" flag actually lives
    is UserPreference.marketing_emails on a real account. So: for a
    logged-in user, this turns that preference on for real. For an
    anonymous visitor there's nowhere to durably record the address, so
    rather than fake a "you're subscribed!" success message for an email
    that goes nowhere, they're pointed at creating an account (which asks
    for marketing-email consent as part of signup).
    """
    if request.user.is_authenticated:
        prefs, _ = UserPreference.objects.get_or_create(user=request.user)
        if not prefs.marketing_emails:
            prefs.marketing_emails = True
            prefs.save(update_fields=['marketing_emails'])
        messages.success(request, "You're subscribed! We'll send travel tips and offers to your account email.")
        return redirect('web_interface:home')

    messages.info(request, "Create a free account to subscribe -- we'll ask for your email there.")
    return redirect('signup:signup')