from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# status thresholds are in one place (scoring.py) - imported here rather than duplicated
from scoring import get_score_rating, COMPLETED_SURAH_THRESHOLD

db = SQLAlchemy()

# Surah table - stores the name and total ayah count for each Surah
class Surah(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    surah_name = db.Column(db.String(100), nullable=False)
    arabic_name = db.Column(db.String(100), nullable=False)
    total_ayahs = db.Column(db.Integer, nullable=False)

    # relationship allows us to access all ayahs belonging to this surah
    ayahs = db.relationship('Ayah', backref='surah', lazy=True, cascade='all, delete-orphan')

# Ayah table - stores each individual verse linked to its surah
class Ayah(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    surah_id = db.Column(db.Integer, db.ForeignKey('surah.id'), nullable=False)
    ayah_number = db.Column(db.Integer, nullable=False)
    arabic_text = db.Column(db.String(500), nullable=False)
    audio_file = db.Column(db.String(200), nullable=False)

# Session table - records every recitation attempt with score and timestamp
class Session(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    surah_id = db.Column(db.Integer, db.ForeignKey('surah.id'), nullable=False)
    ayah_id = db.Column(db.Integer, db.ForeignKey('ayah.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    date_recorded = db.Column(db.DateTime, default=datetime.utcnow)

# AyahProgress table stores each ayah's single most recent recitation score.
# A new submission for an ayah overwrites that ayah's existing row here rather than adding another one 
class AyahProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    surah_id = db.Column(db.Integer, db.ForeignKey('surah.id'), nullable=False)
    ayah_number = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # surah_id + ayah_number together identify one ayah - this is  what stops the same ayah ever ending up with more than one row
    __table_args__ = (
        db.UniqueConstraint('surah_id', 'ayah_number', name='uq_ayah_progress_surah_ayah'), 
    )


def save_ayah_progress(surah_id, ayah_number, score):

    #Look for an existing progress row for this exact ayah first rather than assuming one doesnt exist - this is what makes the function an upsert
    progress = AyahProgress.query.filter_by(
        surah_id=surah_id,
        ayah_number=ayah_number
    ).first()

    #Only create a brand new row if this ayah has never been attempted before
    if progress is None:
        progress = AyahProgress(
            surah_id=surah_id,
            ayah_number=ayah_number
        )
        db.session.add(progress)

    # Existing row is now updated instead of duplicated
    progress.score = round(score, 1)
    progress.timestamp = datetime.utcnow()

    db.session.commit()


def get_ayah_progress(surah_id, ayah_number):
    # Look for this ayah's stored progress row
    progress = AyahProgress.query.filter_by(
        surah_id=surah_id,
        ayah_number=ayah_number
    ).first()

    # Never-attempted ayahs return None rather than crashing
    if progress is None:
        return None

    # Rounded to 1dp
    score = round(progress.score, 1)

    # Status is worked out from the score, not stored in the table
    return {
        'score': score,
        'status': get_score_rating(score),
        'timestamp': progress.timestamp
    }


def get_all_ayah_progress(surah_id):
    # Retrieve attempted ayahs in correct verse order
    rows = AyahProgress.query.filter_by(
        surah_id=surah_id
    ).order_by(
        AyahProgress.ayah_number
    ).all()

    # Build one dict per attempted ayah, with its status worked out here too.
    result = []
    for row in rows:
        score = round(row.score, 1)
        result.append({
            'ayah_number': row.ayah_number,
            'score': score,
            'status': get_score_rating(score),
            'timestamp': row.timestamp
        })

    return result


def calculate_surah_score(surah_id):
    """Calculate a Surah's overall score as the average of every one of its
    Ayah's stored scores - across ALL Ayahs in the Surah, not just the
    attempted ones. An Ayah with no stored score counts as 0 toward the
    average.
    """

    surah = Surah.query.get(surah_id)
    # Handle an unknown surah or one with zero ayahs
    if surah is None or surah.total_ayahs == 0:
        return 0

    attempted = get_all_ayah_progress(surah_id)

    total_score = sum(
        entry['score']
        for entry in attempted
    )

    # Divide by the FULL number of ayahs so unattempted ayahs count as 0
    return round(
        total_score / surah.total_ayahs,
        1
    )

def get_surah_status(surah_id):
    """Work out a Surah's overall progress status from its score and how
    many of its Ayahs have been attempted.
    """

    surah = Surah.query.get(surah_id)

    # unknown Surahs are treated as never started
    if surah is None:
        return 'Not Started'

    score = calculate_surah_score(surah_id)

    if score == 0:
        return 'Not Started'

    # Count how many ayahs in this surah have a stored score
    attempted_count = len(
        get_all_ayah_progress(surah_id)
    )

    # Both conditions must be true to count as Completed
    if (
        score >= COMPLETED_SURAH_THRESHOLD
        and attempted_count == surah.total_ayahs
    ):
        return 'Completed'

    # Anything else is still in progress
    return 'In Progress'

def get_tracker_statistics():
    # Running totals for the four statistics
    completed_count = 0
    started_scores = []
    over_80_count = 0
    under_40_count = 0

    # Go through every surah in the database
    for surah in Surah.query.all():

        score = calculate_surah_score(surah.id)
        status = get_surah_status(surah.id)

        if status == 'Completed':
            completed_count += 1

        # Never-started surahs are not included in average/over-80/under-40 statistics
        if status == 'Not Started':
            continue

        started_scores.append(score)

        if score >= 80:
            over_80_count += 1

        if score < 40:
            under_40_count += 1

    # A completely new user has no started surahs so return zero statistics rather than dividing by zero
    if not started_scores:
        return {
            'completed_count': 0,
            'average_surah_score': 0,
            'over_80_count': 0,
            'under_40_count': 0
        }

    # Average is worked out from started surahs only
    return {
        'completed_count': completed_count,
        'average_surah_score': round(
            sum(started_scores) / len(started_scores),
            1
        ),
        'over_80_count': over_80_count,
        'under_40_count': under_40_count
    }


def get_ayah_breakdown(surah_id):
    surah = Surah.query.get(surah_id)

    if surah is None:
        return []

    # stored scores indexed by ayah number for quick lookup
    attempted_by_number = {
        entry['ayah_number']: entry
        for entry in get_all_ayah_progress(surah_id)
    }

    breakdown = []

    # Go through every ayah number in the surah, not just attempted ones
    for ayah_number in range(1, surah.total_ayahs + 1):
        entry = attempted_by_number.get(ayah_number)

        # No stored score, so this ayah has never been recited
        if entry is None:
            breakdown.append({
                'ayah_number': ayah_number,
                'score': 0,
                'status': 'Not Attempted'
            })
        else:
            breakdown.append({
                'ayah_number': ayah_number,
                'score': entry['score'],
                'status': entry['status']
            })

    return breakdown


def get_weak_ayahs(surah_id, limit=5):
    """Return this Surah's lowest-scoring attempted Ayahs."""

    # uses stored progress only, so unattempted ayahs are never included
    attempted = get_all_ayah_progress(surah_id)

    # Sort lowest score first and cap at the limit
    return sorted(
        attempted,
        key=lambda entry: entry['score']
    )[:limit]


# Progress table - stores the most up to date score and status for each ayah
class Progress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    surah_id = db.Column(db.Integer, db.ForeignKey('surah.id'), nullable=False)
    ayah_id = db.Column(db.Integer, db.ForeignKey('ayah.id'), nullable=False)
    ayah_score = db.Column(db.Integer, default=0)
    surah_score = db.Column(db.Integer, default=0)
    ayah_status = db.Column(db.String(20), default="Weak")
    surah_status = db.Column(db.String(20), default="Not Started")

    # prevents duplicate progress rows for the same ayah - one row is updated/overwritten, never duplicated
    __table_args__ = (db.UniqueConstraint('surah_id', 'ayah_id', name='uq_progress_surah_ayah'),)










    