"""
Populates the database with all 114 Surahs and their Ayahs.

Arabic text is pulled from the public AlQuran Cloud API (quran-uthmani edition)
rather than typed by hand, so the sacred text stored is verified/authoritative
rather than at risk of transcription error. This only needs to run once - after
seeding, the app itself works fully offline for text/navigation (only live
recitation scoring needs internet, to stream/download the reference reciter audio).
"""
import sys
import requests
from app import app
from database.models import db, Surah, Ayah

QURAN_API_URL = "http://api.alquran.cloud/v1/quran/quran-uthmani"


def build_everyayah_code(surah_number, ayah_number):
    return f"{surah_number:03d}{ayah_number:03d}"


def seed_database():
    print(f"Fetching Quran text from {QURAN_API_URL} ...")
    try:
        response = requests.get(QURAN_API_URL, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Failed to fetch Quran data: {exc}")
        sys.exit(1)

    payload = response.json()
    surahs = payload['data']['surahs']
    print(f"Fetched {len(surahs)} surahs. Writing to database...")

    with app.app_context():
        db.drop_all()
        db.create_all()

        for surah_data in surahs:
            surah = Surah(
                id=surah_data['number'],
                surah_name=surah_data['englishName'],
                arabic_name=surah_data['name'],
                total_ayahs=len(surah_data['ayahs']),
            )
            db.session.add(surah)

            for ayah_data in surah_data['ayahs']:
                ayah = Ayah(
                    surah_id=surah_data['number'],
                    ayah_number=ayah_data['numberInSurah'],
                    arabic_text=ayah_data['text'],
                    audio_file=build_everyayah_code(surah_data['number'], ayah_data['numberInSurah']),
                )
                db.session.add(ayah)

        db.session.commit()
        print(f"Database seeded successfully: {Surah.query.count()} surahs, {Ayah.query.count()} ayahs.")


if __name__ == '__main__':
    seed_database()
