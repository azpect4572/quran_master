import os
from datetime import datetime

import requests
from flask import Flask, render_template, request, abort, jsonify
from sqlalchemy import or_

from database.models import (
    db, Surah, Ayah, save_ayah_progress, get_all_ayah_progress,
    calculate_surah_score, get_surah_status, get_tracker_statistics,
    get_ayah_breakdown, get_weak_ayahs
)

from audio_utils import RECORDINGS_DIR, build_audio_url, RECITERS

from scoring import (
    load_reference_audio,
    load_user_recording,
    extract_features,
    compare_feature,
    compare_pause,
    calculate_weighted_score,
    get_score_rating
)


# App creation & configuration
app = Flask(__name__)

# links the app to an SQLite database file called quran_master.db
# Cloud Run filesystem is ephemeral - data resets on new revision. Use Firestore for permanent prod data.
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', 'sqlite:///quran_master.db'
)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'supersecretkey')


# Database wiring
# binds the SQLAlchemy instance (defined in database/models.py) to this app
db.init_app(app)

# Production startup (runs under gunicorn on Cloud Run, not just __main__):
# create tables and seed Quran text if the image started without data
# (Cloud Run filesystem is ephemeral - data resets on new revision).
os.makedirs(app.instance_path, exist_ok=True)
os.makedirs(RECORDINGS_DIR, exist_ok=True)
with app.app_context():
    db.create_all()
    try:
        if Surah.query.count() == 0:
            app.logger.info('Empty database - seeding Quran text from AlQuran Cloud API...')
            resp = requests.get(
                'http://api.alquran.cloud/v1/quran/quran-uthmani', timeout=60
            )
            resp.raise_for_status()
            for surah_data in resp.json()['data']['surahs']:
                db.session.add(Surah(
                    id=surah_data['number'],
                    surah_name=surah_data['englishName'],
                    arabic_name=surah_data['name'],
                    total_ayahs=len(surah_data['ayahs']),
                ))
                for ayah_data in surah_data['ayahs']:
                    db.session.add(Ayah(
                        surah_id=surah_data['number'],
                        ayah_number=ayah_data['numberInSurah'],
                        arabic_text=ayah_data['text'],
                        audio_file=f"{surah_data['number']:03d}{ayah_data['numberInSurah']:03d}",
                    ))
            db.session.commit()
            app.logger.info('Database seeded successfully.')
    except Exception as exc:
        # Never crash startup because seeding failed - routes handle empty DB gracefully
        app.logger.warning('Database seed skipped/failed: %s', exc)

# Maps a Surah's status word (from get_surah_status()) onto the same
# rating-colour classes the Ayah rating labels already use 
SURAH_STATUS_RATING_CLASS = {
    'Completed': 'rating-good',
    'In Progress': 'rating-satisfactory',
    'Not Started': 'rating-not-attempted',
}


# Routes
# Health check for Cloud Run
@app.route('/healthz')
def healthz():
    return 'ok', 200


# Homepage - renders the page with the "Surah Search","Progress Tracker" and "Help" buttons, linking to /surahs, /progress and /help
@app.route('/')
def home():
    return render_template('home.html')


# Help page - a guide covering surah search, the recitation screen's
# controls, reciters, how scoring works, and the progress tracker. 
@app.route('/help')
def help_page():
    return render_template('help.html')


# Surah search page - lists every surah, or if a search
# term is given via the "q" query parameter (e.g. /surahs?q=fatiha).
@app.route('/surahs')
def surah_selection():
    # request.args.get() returns None if "q" wasn't supplied in the URL
    search_term = request.args.get('q', default=None, type=str)

    if search_term:
        # match if the name contains the search term (case-insensitive)
        conditions = [Surah.surah_name.ilike(f'%{search_term}%')]

        # also match by surah number, if the search term is a valid integer
        try:
            search_number = int(search_term)
            conditions.append(Surah.id == search_number)
        except ValueError:
            pass  # not a number - skip the number match, name match still applies

        # or_() returns a surah if either condition matches
        surahs = Surah.query.filter(or_(*conditions)).order_by(Surah.id).all()
    else:
        # when there is no search term - returns every surah in order
        surahs = Surah.query.order_by(Surah.id).all()

    # each card's "Score: %" line - same calculate_surah_score() already
    # used on the progress tracker page, keyed by id so the template can
    # look a score up for each card without a second query per surah
    surah_scores = {
        surah.id: calculate_surah_score(surah.id)
        for surah in surahs
    }

    return render_template(
        'surah_selection.html',
        surahs=surahs,
        search_term=search_term,
        surah_scores=surah_scores
    )


@app.route('/recitation/<int:surah_id>')
def recitation(surah_id):
    # look up the surah by id - returns None if it doesn't exist
    surah = Surah.query.get(surah_id)
    if surah is None:
        abort(404, description=f"Surah with id {surah_id} was not found.")

    # get all ayahs belonging to this surah, in correct verse order
    ayahs = Ayah.query.filter_by(surah_id=surah.id).order_by(Ayah.ayah_number).all()
    raw_ayah = request.args.get('ayah')

    if raw_ayah is None:
        # genuinely missing from the URL - default to ayah 1
        current_ayah_number = 1
    else:
        try:
            # try to convert the given value to an integer
            current_ayah_number = int(raw_ayah)
        except ValueError:
            # supplied but not a valid number (e.g. ?ayah=abc) - rejected
            abort(404, description=f'"{raw_ayah}" is not a valid Ayah number.')

    # reject out-of-range ayah numbers with a 404, rather than silently clamping them
    if current_ayah_number < 1 or current_ayah_number > surah.total_ayahs:
        abort(
            404,
            description=(
                f"Ayah {current_ayah_number} does not exist in Surah "
                f"{surah.surah_name} (valid range: 1-{surah.total_ayahs})."
            )
        )

    
   # get the "reciter" query param, defaulting to "alafasy" if not given
    reciter_key = request.args.get('reciter', default='alafasy', type=str)
    reciter_display_name, _ = RECITERS.get(reciter_key, RECITERS['alafasy'])

    # if the key itself was invalid, default it to "alafasy" too, so the
    # template's dropdown and any generated URLs stay consistent
    if reciter_key not in RECITERS:
        reciter_key = 'alafasy'

    # build the EveryAyah mp3 URL for exactly what's currently selected
    audio_url = build_audio_url(surah.id, current_ayah_number, reciter_key)

    # stored progress for this surah - read back what save_ayah_progress()
    # has saved, rather than the page resetting every Ayah to "Not attempted" every time
    all_ayah_progress = get_all_ayah_progress(surah.id)

    # keyed by ayah_number so the template can look up each ayah card's own
    # entry in O(1) instead of scanning the list per card
    ayah_progress_by_number = {
        entry['ayah_number']: entry for entry in all_ayah_progress
    }
    current_ayah_progress = ayah_progress_by_number.get(current_ayah_number)

    surah_score = calculate_surah_score(surah.id)
    surah_status = get_surah_status(surah.id)

    # render the recitation UI
    return render_template(
        'recitation.html',
        surah=surah,
        ayahs=ayahs,
        current_ayah_number=current_ayah_number,
        reciter_key=reciter_key,
        reciter_display_name=reciter_display_name,
        reciters=RECITERS,
        audio_url=audio_url,
        ayah_progress_by_number=ayah_progress_by_number,
        current_ayah_progress=current_ayah_progress,
        surah_score=surah_score,
        surah_status=surah_status,
        surah_status_class=SURAH_STATUS_RATING_CLASS.get(surah_status, 'rating-not-attempted')
        )

@app.route('/submit_recitation/<int:surah_id>/<int:ayah_number>', methods=['POST'])
def submit_recitation(surah_id, ayah_number):

    # reject the request if no recording field was included at all
    if 'recording' not in request.files:
        return jsonify(
            error='No audio file was included in the request.'
        ), 400

    audio_file = request.files['recording']

    # reject the request if the field exists but no actual file was chosen
    if audio_file.filename == '':
        return jsonify(
            error='No audio file was included in the request.'
        ), 400

    # create the recordings folder if it doesn't already exist
    # rather than assuming it's always present
    os.makedirs(RECORDINGS_DIR, exist_ok=True)

    # use the extension from the uploaded file itself (e.g. ".webm"),
    #falls back to .webm only if the browser somehow sent a filename with no extension at all
    _, extension = os.path.splitext(audio_file.filename)
    if not extension:
        extension = '.webm'

    # timestamp must be generated before it's used in filename below
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')

    # build a unique filename from surah + ayah + timestamp
    # repeated recordings of the same ayah never overwrite each other
    filename = f'{surah_id}_{ayah_number}_{timestamp}{extension}'

    save_path = os.path.join(RECORDINGS_DIR, filename)

    # save the uploaded audio file to disk at the path built above
    audio_file.save(save_path)

    # TEMPORARY TESTING FOR SCORING SYSTEM

    # Load the professional reference recitation
    ref_y, ref_sr = load_reference_audio(
        surah_id,
        ayah_number
    )

    # Load the recording that has just been submitted, and extract its features
    try:
        user_y, user_sr = load_user_recording(
            surah_id,
            ayah_number,
            timestamp,
            extension
        )
        user_features = extract_features(user_y, user_sr)
    except Exception:
        return jsonify(
            error='Recording too short, please try again'
        ), 400

    # Extract features from the reference audio
    ref_features = extract_features(ref_y, ref_sr)

    # Print raw features to terminal
    print(
        f"Reference - "
        f"Duration: {ref_features['duration']:.1f}s, "
        f"Pauses: {ref_features['pause_duration']:.1f}s, "
        f"Amplitude: {ref_features['amplitude']:.2f}"
    )

    print(
        f"User - "
        f"Duration: {user_features['duration']:.1f}s, "
        f"Pauses: {user_features['pause_duration']:.1f}s, "
        f"Amplitude: {user_features['amplitude']:.2f}"
    )

    # Compare features and print similarity percentages + final weighted score
    duration_similarity = compare_feature(
        ref_features['duration'], user_features['duration']
    )
    pause_similarity = compare_pause(
        ref_features['pause_duration'], user_features['pause_duration']
    )
    amplitude_similarity = compare_feature(
        ref_features['amplitude'], user_features['amplitude']
    )
    weighted_score = calculate_weighted_score(ref_features, user_features)

    # save this ayah's latest score - replaces any previous score already stored for the surah/ayah
    save_ayah_progress(surah_id, ayah_number, weighted_score)

    print(f"Duration similarity: {duration_similarity:.0f}%")
    print(f"Pause similarity: {pause_similarity:.0f}%")
    print(f"Amplitude similarity: {amplitude_similarity:.0f}%")
    print(f"Weighted score: {weighted_score:.1f}%")

    rating = get_score_rating(weighted_score)
    print(f"Rating: {rating}")

    # this ayah's new score has already been saved
    # the frontend uses these to update the Surah Score card without a second request
    surah_score = calculate_surah_score(surah_id)
    surah_status = get_surah_status(surah_id)

    return jsonify(
        success=True,
        filename=filename,
        weighted_score=round(weighted_score, 1),
        rating=rating,
        surah_score=surah_score,
        surah_status=surah_status
    ), 200


# Progress tracker page - four summary statistics plus every surah's score/status 
@app.route('/progress')
def progress():
    stats = get_tracker_statistics()

    surahs = Surah.query.order_by(Surah.id).all()

    surah_progress = []
    for surah in surahs:
        score = calculate_surah_score(surah.id)
        status = get_surah_status(surah.id)
        surah_progress.append({
            'id': surah.id,
            'surah_name': surah.surah_name,
            'score': score,
            'status': status,
            # same status -> colour mapping the recitation page's Surah
            # Score card already uses
            'status_class': SURAH_STATUS_RATING_CLASS.get(status, 'rating-not-attempted')
        })

    return render_template(
        'progress.html',
        stats=stats,
        surah_progress=surah_progress
    )


# when a Surah is clicked, to load that surah's ayah breakdown + weakest ayahs
@app.route('/progress/ayah_breakdown/<int:surah_id>')
def progress_ayah_breakdown(surah_id):
    surah = Surah.query.get(surah_id)
    if surah is None:
        abort(404, description=f"Surah with id {surah_id} was not found.")

    weak_ayahs = [
        {'ayah_number': entry['ayah_number'], 'score': entry['score'], 'status': entry['status']}
        for entry in get_weak_ayahs(surah_id)
    ]

    return jsonify(
        surah_name=surah.surah_name,
        breakdown=get_ayah_breakdown(surah_id),
        weak_ayahs=weak_ayahs
    )


# Error handlers
@app.errorhandler(404)
def not_found(error):
    message = getattr(error, 'description', 'Page not found.')
    return render_template('error.html', message=message), 404


@app.errorhandler(500)
def server_error(error):
    return render_template('error.html', message='Something went wrong. Please try again.'), 500


# Entry point
if __name__ == '__main__':
    # creates any tables that don't already exist yet (existing data is left untouched)
    with app.app_context():
        db.create_all()
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)


