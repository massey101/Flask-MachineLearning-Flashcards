from flask import render_template, redirect, url_for, abort, flash, jsonify, make_response, request, current_app
from flask_login import login_required, current_user
from flask_sqlalchemy import get_debug_queries
from ..models.users import User
from ..models.category import Category
from ..models.flashcard_collections import FlashcardCollection
from ..models.flashcard import Flashcard
from . import main
from .. import db
from .forms import FlashcardCollectionForm, FlashcardForm, EditFlashcardForm
from random import choice
from random import shuffle
import datetime
from collections import Counter
import math
import copy
import pickle
import sqlite3
import operator
from random import randint

#correct, wrong, exponential, intercept/bias
WEIGHTS = [-1.31889574, -0.46632819,  3.63402041, 6.61932582385]

#mins
SESSION_LENGTH = 30
REP_PER_MIN = 7
DESIGN = 2

flashcard_generated = {}

def loadPickle(fname):
    with open(fname, 'rb') as handle:
        item = pickle.load(handle)
    return item

@main.after_app_request
def after_request(response):
    for query in get_debug_queries():
        if query.duration >= current_app.config['FLASHCARD_SLOW_DB_QUERY_TIME']:
            current_app.logger.warning(
                'Slow query: %s\nParameters: %s\nDuration: %fs\nContext: %s\n' %
                (query.statement, query.parameters, query.duration, query.context))
    return response


@main.route('/')
def index():
    if current_user.is_authenticated:
        collections = current_user.collections.order_by(FlashcardCollection.timestamp.desc()).all()
    else:
        collections = []
    return render_template('index.html', collections=collections)


@main.route('/user/<username>')
@login_required
def user(username):
    user = User.query.filter_by(username=username).first()
    if user is None:
        abort(404)
    collections = current_user.collections.order_by(FlashcardCollection.timestamp.desc()).all()
    return render_template('user.html', user=user, collections=collections)


@main.route('/add-collection', methods=['GET', 'POST'])
@login_required
def add_collection():
    form = FlashcardCollectionForm()
    if form.validate_on_submit():
        category = Category.query.filter_by(name=form.category.data).first()
        if category is None:
            category = Category(name=form.category.data)
        collection = FlashcardCollection(name=form.name.data)
        collection.categories.append(category)
        collection.user = current_user
        db.session.add(collection)
        db.session.commit()
        flash('Flashcard Collection added.')
        return redirect(url_for('.index'))
    return render_template('add_collection.html', form=form)


@main.route('/get-category', methods=['GET', 'POST'])
@login_required
def get_category():
    return jsonify({
        'category': [category.name for category in Category.query.order_by(Category.name).all()]
    })


@main.route('/flashcardcollection/<int:id>')
@login_required
def flashcardcollection(id):
    flashcardcollection = FlashcardCollection.query.get_or_404(id)
    return render_template('flashcardcollection.html', flashcardcollection=flashcardcollection)


@main.route('/flashcardcollection/<int:id>/delete')
@login_required
def delete_flashcardcollection(id):
    flashcardcollection = FlashcardCollection.query.get_or_404(id)
    db.session.delete(flashcardcollection)
    db.session.commit()
    flash('Flashcardcollection {0} has been deleted'.format(flashcardcollection.name))
    return redirect(request.referrer)


@main.route('/flashcardcollection/<int:id>/add-flashcard', methods=['GET', 'POST'])
@login_required
def add_flashcard(id):
    form = FlashcardForm()
    flashcardcollection = FlashcardCollection.query.get_or_404(id)
    if form.validate_on_submit():
        card = Flashcard(question=form.question.data, answer=form.answer.data)
        flashcardcollection.flashcards.append(card)
        db.session.add(flashcardcollection)
        db.session.commit()
        flash('Flashcard added to the Collection {0}'.format(flashcardcollection.name))
        if form.next.data:
            return redirect(url_for('.add_flashcard', id=flashcardcollection.id))
        else:
            return redirect(url_for('.flashcardcollection', id=flashcardcollection.id))
    return render_template('add_flashcard.html', form=form, name=flashcardcollection.name)


@main.route('/flashcardcollection/<int:collId>/flashcard/<int:cardId>')
@login_required
def flashcard(collId, cardId):
    flashcardcollection = FlashcardCollection.query.get_or_404(collId)
    flashcard = flashcardcollection.flashcards.filter_by(id=cardId).first()
    if flashcard is None:
        abort(404)
    return render_template('flashcard.html', flashcardcollection=flashcardcollection, flashcard=flashcard)


@main.route('/flashcardcollection/<int:collId>/flashcard/<int:cardId>/edit', methods=['GET', 'POST'])
@login_required
def edit_flashcard(collId, cardId):
    form = EditFlashcardForm()
    flashcardcollection = FlashcardCollection.query.get_or_404(collId)
    flashcard = flashcardcollection.flashcards.filter_by(id=cardId).first()
    if flashcard is None:
        abort(404)
    if form.validate_on_submit():
        flashcard.question = form.question.data
        flashcard.answer = form.answer.data
        db.session.add(flashcard)
        db.session.commit()
        flash('Flashcard was updated.')
        return redirect(url_for('.flashcard', collId=collId, cardId=cardId))
    form.question.data = flashcard.question
    form.answer.data = flashcard.answer
    return render_template('edit_flashcard.html', form=form, flashcard=flashcard)


@main.route('/flashcardcollection/<int:id>/learn/<int:current>')
@login_required
def learn(id, current):
    def pclip(p):
        return min(max(p, 0.1), .9999)
    def hclip(h):
        return min(max(h, 1), 2000000)

    #important vars
    flashcardcollection = FlashcardCollection.query.get_or_404(id)
    flashcards = flashcardcollection.flashcards.all()
    mode = request.args.get('mode')

    sqlite_file = 'data-dev.sqlite'
    user_ids = []
    score = []

    conn = sqlite3.connect(sqlite_file)
    c = conn.cursor()

    for row in c.execute("SELECT rowid, * FROM users"):
        user_ids.append(row[3])
        score.append(row[-9])

    pre_dict = dict(zip(user_ids, score))
    leaderboards = dict(reversed(sorted(pre_dict.items(), key=lambda x:x[1])))

    #temp vars
    total_repetitions = SESSION_LENGTH*REP_PER_MIN
    scheduler = 1
    repetitions_per_scheduler = round(total_repetitions/DESIGN)

    for i in range(len(flashcards)):
        if flashcards[i].history == '' or flashcards[i].last_time == 0:
            #print(str(i+1))
            flashcard_generated[i+1] = 0
        else:
            #generic features
            history = [int(x) for x in flashcards[i].history.split(',')]
            correct = Counter(history)[1]
            wrong = Counter(history)[0]
            time_elapsed = int(datetime.datetime.now().strftime('%s')) - flashcards[i].last_time
            last_strength = flashcards[i].last_strength

            expo = 0.0

            if (len(history) > 1):
                for y in range(len(history)):
                    expo_incre = math.pow(0.8,y)
                    if list(reversed(history))[y] == 1.0:
                        expo += expo_incre
            else:
                expo = 0.0

            #scale the features
            scaled_correct = math.sqrt(1.0+correct)
            scaled_wrong = math.sqrt(1.0 + wrong)
            scaled_expo = math.sqrt(1.0 + expo)
            h_power = (scaled_correct*WEIGHTS[0])+(scaled_wrong*WEIGHTS[1])+(scaled_expo*WEIGHTS[2])+WEIGHTS[3]
            h = hclip(math.pow(2, h_power))
            p = pclip(math.pow(2, (-time_elapsed)/h))

            #assign probability to array
            flashcard_generated[i+1] = p

    learning_cards = copy.copy(flashcard_generated)
    new_cards = copy.copy(flashcard_generated)
    for i in range(len(flashcard_generated)):
        if flashcard_generated[i+1] == 0:
            learning_cards.pop(i+1)
        else:
            new_cards.pop(i+1)
    if current == 0:
        if len(learning_cards) == 0:
            if scheduler != 3:
                flashcard = flashcards[choice(list(new_cards.keys()))-1]
            else:
                flashcard = flashcards[0]
            current_user.last_index = flashcards.index(flashcard)
        else:
            index = min(learning_cards, key=learning_cards.get)
            difference = 0.5-learning_cards[index]
            
            if difference < -0.4 and len(new_cards) > 0:
                flashcard = flashcards[choice(list(new_cards.keys()))-1]
            else:
                flashcard = flashcards[index-1]
    else:
        flashcard = Flashcard.query.get_or_404(current)

    current_user.last_index = flashcards.index(flashcard)
    current_user.last_time = int(datetime.datetime.now().strftime('%s'))
    flashcard.start_learn_time = int(datetime.datetime.now().strftime('%s'))

    chance = round(round(flashcard_generated[flashcards.index(flashcard)+1],2)*100)-11
    overall_sum = sum(flashcard_generated.values())
    overall_len = len(flashcard_generated.values())
    seen = 0
    for i in range(len(flashcards)):
        if flashcard_generated[i+1] > 0:
            seen += 1

    time_left = SESSION_LENGTH - current_user.total_reps/7
    time_left = round(time_left,2)

    if flashcard.history == '' and flashcard.pre_answer != 1:
        return render_template('pretest.html', flashcard=flashcard, collection=flashcardcollection, overall_sum=overall_sum, overall_len=overall_len, seen=seen, time_left=time_left, leaderboards=leaderboards)

    return render_template('learn.html', flashcard=flashcard, collection=flashcardcollection, chance=chance, overall_sum=overall_sum, overall_len=overall_len, seen=seen, time_left=time_left, leaderboards=leaderboards)

@main.route('/flashcardcollection/<int:id>/test')
@login_required
def test(id):
    #important vars
    flashcardcollection = FlashcardCollection.query.get_or_404(id)
    flashcards = flashcardcollection.flashcards.all()
    mode = request.args.get('mode')
    flashcard = flashcards[0]

    if current_user.last_index == len(flashcards)-1:
        print("done")
    elif current_user.last_index == 0 and flashcard.test_answer == -1:
        flashcard = flashcards[0]
    else:
        flashcard = flashcards[current_user.last_index+1]

    current_user.last_index = flashcards.index(flashcard)

    return render_template('test.html', flashcard=flashcard, collection=flashcardcollection)

@main.route('/flashcardcollection/<int:id>/pretest')
@login_required
def pretest(id):
    #important vars
    flashcardcollection = FlashcardCollection.query.get_or_404(id)
    flashcards = flashcardcollection.flashcards.all()
    mode = request.args.get('mode')
    flashcard = flashcards[0]



    if current_user.last_index == len(flashcards)-1:
        return redirect(url_for('.learn', id=flashcardcollection.id, mode='start'))
    elif current_user.last_index == 0 and flashcard.pre_answer == -1 or flashcard.pre_answer == None:
        flashcard = flashcards[0]
    else:
        flashcard = flashcards[current_user.last_index+1]

    current_user.last_index = flashcards.index(flashcard)

    return render_template('pretest.html', flashcard=flashcard, collection=flashcardcollection)


@main.route('/<int:id>/questions/<int:cycle>/<int:set_id>')
@login_required
def questions(id, cycle, set_id):
    if cycle:
        flash('Please fill in ALL the fields.')
    return render_template('questions.html', id=id, set_id=set_id)

@main.route('/submit/<int:id>/<int:set_id>')
def submit(id, set_id):
    field1 = request.args.get('field1')
    if set_id == 4:
        spec = request.args.get('spec')
        field2 = request.args.get('field2')
        field3 = request.args.get('field3')
        field4 = request.args.get('field4')
        field5 = request.args.get('field5')
        field6 = request.args.get('field6')
        field7 = request.args.get('field7')
        feedback = spec + ',' + field1 + ',' + field2 + ',' + field3 + ',' + field4 + ',' + field5 + ',' + field6 + ',' + field7
    else:
        feedback = field1

    if set_id == 2:
        current_user.feedback_1 = feedback
    elif set_id == 3:
        current_user.feedback_2 = feedback
    else:
        current_user.feedback_3 = feedback

    if set_id == 4:
        if spec == '' or field1 == '' or field2 == '' or field3 == '' or field4 == '' or field5 == '' or field6 == '' or field7 == '':
            return redirect(url_for('.questions', id=id, cycle=1, set_id=set_id))
        else:
            return redirect(url_for('.pause', id=id, start=0, ready=1, set_id=set_id))
    else:
        if field1 == '':
            return redirect(url_for('.questions', id=id, cycle=1, set_id=set_id))
        else:
            return redirect(url_for('.pause', id=id, start=0, ready=1, set_id=set_id))

@main.route('/flashcardcollection/<int:id>/<int:start>/<int:ready>/<int:set_id>/pause')
@login_required
def pause(id, start, ready, set_id):
    current_user.set_num += 1
    if start:
        current_user.started = 1
    return render_template('pause.html', id=id, start=start, ready=ready, set_id=set_id)

@main.route('/flashcardcollection/<int:id>/consent')
@login_required
def consent(id):
    return render_template('consent.html', id=id)

@main.route('/realtime')
def realtime():
    sqlite_file = 'data-dev.sqlite'
    user_ids = []
    values = []

    conn = sqlite3.connect(sqlite_file)
    c = conn.cursor()

    for row in c.execute("SELECT rowid, * FROM users"):
        user_ids.append(row[3])
        values.append(str(row[-2]) + ',' + str(row[-9]) + ',' + str(row[-11]))

    ls = dict(zip(user_ids, values))
    return render_template('realtime.html', ls=ls)


@main.route('/backend/<int:db>')
def backend(db):
    def pclip(p):
        return min(max(p, 0.1), .9999)
    def hclip(h):
        return min(max(h, 1), 2000000)

    
    # sqlite_file = 'classroom-data/experiment.sqlite'
    sqlite_file = 'data-dev.sqlite'

    conn_ = sqlite3.connect(sqlite_file)
    c_ = conn_.cursor()
    users = {};

    user_predictions = [];
    user_cards = [];
    user_colours = [];
    user_memoryscores = [];
    real_user_predictions = [];
    cards = [];

    for row in c_.execute("SELECT rowid, * FROM users"):
        users[int(row[0])] = str(row[-9])

    for user_id in users:
        #important vars
        flashcardcollection = FlashcardCollection.query.get_or_404(user_id)
        flashcards = flashcardcollection.flashcards.all()

        scheduler = 1

        predictions = {};   
        real_predictions = {};             

        for i in range(len(flashcards)):
            if user_id == 1:
                cards.append(flashcards[i].question);
            if flashcards[i].history == '' or flashcards[i].last_time == 0:
                #print(str(i+1))
                predictions[i+1] = 0
            else:
                #generic features
                history = [int(x) for x in flashcards[i].history.split(',')]
                correct = Counter(history)[1]
                wrong = Counter(history)[0]
                time_elapsed = int(datetime.datetime.now().strftime('%s')) - flashcards[i].last_time
                last_strength = flashcards[i].last_strength

                #machine learning features
                if scheduler == 1:
                    expo = 0.0

                    if (len(history) > 1):
                        for y in range(len(history)):
                            expo_incre = math.pow(0.8,y)
                            if list(reversed(history))[y] == 1.0:
                                expo += expo_incre
                    else:
                        expo = 0.0

                    #scale the features
                    scaled_correct = math.sqrt(1.0+correct)
                    scaled_wrong = math.sqrt(1.0 + wrong)
                    scaled_expo = math.sqrt(1.0 + expo)
                    h_power = (scaled_correct*WEIGHTS[0])+(scaled_wrong*WEIGHTS[1])+(scaled_expo*WEIGHTS[2])+WEIGHTS[3]
                    h = hclip(math.pow(2, h_power))
                    p = pclip(math.pow(2, (-time_elapsed)/(h*0.2)))
                    real_p = pclip(math.pow(2, (-time_elapsed)/(h)))

                #assign probability to array
                predictions[i+1] = p
                real_predictions[i+1] = real_p;


        # print(predictions);
        current_cards = {};
        colour = {};

        conn = sqlite3.connect(sqlite_file)
        c = conn.cursor()
        for row in c.execute("SELECT rowid, * FROM flashcard"):
            
            if row[6] == user_id:
                if row[8] != '':
                    current_cards[row[1]-(30*(user_id-1))] = row[4] + ','+row[2];
                    if predictions[row[1]-(30*(user_id-1))] > 0.6:
                        colour[row[1]-(30*(user_id-1))] = 'green';
                    else: 
                        colour[row[1]-(30*(user_id-1))] = 'red';

        # memory scores
        memory_score = 0;
        for i in predictions:
            memory_score += (predictions[i])


        user_predictions.append(predictions);
        real_user_predictions.append(real_predictions);
        user_colours.append(colour);
        user_cards.append(current_cards);
        user_memoryscores.append(memory_score);

    # print(flashcard_generated)

    return render_template('backend.html', db=db, current_cards = user_cards, predictions = user_predictions, real_predictions = real_user_predictions, colour=user_colours, users=users, memory_scores=user_memoryscores, cards=cards)

@main.route('/flashcardcollection/<int:id>/reset-cards')
@login_required
def reset_cards(id):
    coll = FlashcardCollection.query.get_or_404(id)
    current_user.total_reps = 0
    current_user.last_index = 0
    current_user.score = 0
    current_user.set_num = 1
    current_user.feedback_1 = ''
    current_user.feedback_2 = ''
    current_user.feedback_3 = ''
    current_user.sequential_cycle = 1
    current_user.started = 0
    current_user.last_time = 0

    for card in coll.flashcards.all():
        card.history = ''
        card.last_time = 0
        card.time_history = ''
        card.timestamps = ''
        card.durations = ''
        card.test_answer = -1
        card.pre_answer = -1
        card.last_strength = 0
        card.introduced_history = ''
        card.start_learn_time = 0

    db.session.add(coll)
    db.session.commit()
    return redirect(url_for('.flashcardcollection', id=id))


@main.route('/flashcardcollection/<int:collId>/delete-flashcard/<int:cardId>')
@login_required
def delete_card(collId, cardId):
    flashcard = Flashcard.query.get_or_404(cardId)
    db.session.delete(flashcard)
    db.session.commit()
    return redirect(url_for('.flashcardcollection', id=collId))


@main.route('/flashcardcollection/<int:collId>/learn/<int:cardId>/wrong/<int:duration>')
@login_required
def wrong_answer(collId, cardId, duration):
    print('response' + str(duration))
    flashcard = Flashcard.query.get_or_404(cardId)
    current_time = int(datetime.datetime.now().strftime('%s'))

    if flashcard.history == '':
        if flashcard.introduced_history == '':
            flashcard.introduced_history += str(current_time)
        else:
            flashcard.introduced_history += ',' + str(current_time)

    if flashcard.history == '':
        flashcard.history = flashcard.history + '0'
    else:
        flashcard.history += ',0'

    if flashcard.time_history == '':
        flashcard.time_history += '0'
    else:
        flashcard.time_history += ',' + str(current_time-int(flashcard.last_time))

    if flashcard.timestamps == '':
        flashcard.timestamps += str(current_time)
    else:
        flashcard.timestamps += ',' + str(current_time)

    if flashcard.durations == '':
        flashcard.durations += str(current_time-flashcard.start_learn_time-3)
    else:
        flashcard.durations += ',' + str(current_time-flashcard.start_learn_time-3)

    if flashcard.last_strength != 0:
        flashcard.last_strength -= 1
    current_user.total_reps += 1
    current_user.score += 1
    flashcard.last_time = current_time
    db.session.add(flashcard)
    db.session.commit()
    return redirect(url_for('.learn', id=collId, current=0, mode='normal'))

@main.route('/flashcardcollection/<int:collId>/learn/<int:cardId>/right/<int:duration>')
@login_required
def right_answer(collId, cardId, duration):
    flashcard = Flashcard.query.get_or_404(cardId)
    current_time = int(datetime.datetime.now().strftime('%s'))

    if flashcard.history == '':
        if flashcard.introduced_history == '':
            flashcard.introduced_history += str(current_time)
        else:
            flashcard.introduced_history += ',' + str(current_time)

    if flashcard.history == '':
        flashcard.history = flashcard.history + '1'
    else:
        flashcard.history += ',1'

    if flashcard.time_history == '':
        flashcard.time_history += '0'
    else:
        flashcard.time_history += ',' + str(current_time-int(flashcard.last_time))

    if flashcard.timestamps == '':
        flashcard.timestamps += str(current_time)
    else:
        flashcard.timestamps += ',' + str(current_time)

    if flashcard.durations == '':
        flashcard.durations += str(current_time-flashcard.start_learn_time-0.5)
    else:
        flashcard.durations += ',' + str(current_time-flashcard.start_learn_time-0.5)

    flashcard.last_strength += 1
    current_user.total_reps += 1
    current_user.score += 3
    flashcard.last_time = current_time
    db.session.add(flashcard)
    db.session.commit()
    return redirect(url_for('.learn', id=collId, current=0, mode='normal'))

@main.route('/flashcardcollection/<int:collId>/test/<int:cardId>/wrong/<int:duration>')
@login_required
def test_wrong(collId, cardId, duration):
    flashcard = Flashcard.query.get_or_404(cardId)

    flashcard.test_answer = 0

    db.session.add(flashcard)
    db.session.commit()
    return redirect(url_for('.test', id=collId, mode='normal'))

@main.route('/flashcardcollection/<int:collId>/test/<int:cardId>/right/<int:duration>')
@login_required
def test_right(collId, cardId, duration):
    flashcard = Flashcard.query.get_or_404(cardId)

    flashcard.test_answer = 1

    db.session.add(flashcard)
    db.session.commit()
    return redirect(url_for('.test', id=collId, mode='normal'))

@main.route('/flashcardcollection/<int:collId>/test/<int:cardId>/next')
@login_required
def next(collId, cardId):
    flashcardcollection = FlashcardCollection.query.get_or_404(collId)
    flashcard = Flashcard.query.get_or_404(cardId)

    flashcard.pre_answer = 1

    db.session.add(flashcard)
    db.session.commit()
    return redirect(url_for('.learn', id=collId, current=cardId, mode='normal'))
