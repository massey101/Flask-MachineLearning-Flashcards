from .. import db
from markdown import markdown
import bleach


class Flashcard(db.Model):
    __tablename__ = 'flashcard'
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.Text)
    question_html = db.Column(db.Text)
    answer = db.Column(db.Text)
    answer_html = db.Column(db.Text)
    history = db.Column(db.Text, default='')
    time_history = db.Column(db.Text, default='0')
    last_time = db.Column(db.Integer, default=0)
    timestamps = db.Column(db.Text, default='')
    durations = db.Column(db.Text, default='')
    scheduler = db.Column(db.Integer, default = 1)
    test_answer = db.Column(db.Integer, default = -1)
    pre_answer = db.Column(db.Integer, default = -1)
    last_strength = db.Column(db.Integer, default = 0)
    introduced_history = db.Column(db.Text, default = '')
    actual_response = db.Column(db.Text, default = '')
    start_learn_time = db.Column(db.Integer)
    collection_id = db.Column(db.Integer, db.ForeignKey('flashcardcollection.id'))

    @staticmethod
    def on_changed_question(target, value, oldvalue, initiator):
        allowed_tags = ['abbr', 'acronym', 'b', 'blockquote', 'code', 'i',
                        'li', 'ol', 'strong', 'ul', 'h1', 'h2', 'h3', 'p']
        target.question_html = bleach.clean(markdown(value, output_format='html'), tags=allowed_tags, strip=True)

    @staticmethod
    def on_changed_answer(target, value, oldvalue, initiator):
        allowed_tags = ['abbr', 'acronym', 'b', 'blockquote', 'code', 'i',
                        'li', 'ol', 'strong', 'ul', 'h1', 'h2', 'h3', 'p']
        target.answer_html = bleach.clean(markdown(value, output_format='html'), tags=allowed_tags, strip=True)

    def __repr__(self):
        return '<Flashcard: %r>' % self.id

db.event.listen(Flashcard.answer, 'set', Flashcard.on_changed_answer)
db.event.listen(Flashcard.question, 'set', Flashcard.on_changed_question)
