from flask import Flask, render_template, url_for, flash, redirect
from flask import request, jsonify
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, relationship, joinedload
from database_setup import Base, Restaurant, MenuItem, User
from flask import make_response, flash
from oauth2client.client import flow_from_clientsecrets, FlowExchangeError
from flask import session as login_session
import httplib2
import requests
import json
import random
import string


CLIENT_ID = json.loads(
    open('client_secrets.json', 'r').read())['web']['client_id']

app = Flask(__name__)

app.config['SECRET_KEY'] = '6d22391e94319de409da02ff3f19dd83'

# engine = create_engine('sqlite:///restaurantmenu.db?check_same_thread=False')
engine = create_engine('postgresql://restaurant:1433@localhost/restaurant')
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()


# Create anti-forgery state token
@app.route('/login')
def showLogin():
    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    login_session['state'] = state
    # return "The current session state is %s" % login_session['state']
    return render_template('login.html', STATE=state)


@app.route('/gconnect', methods=['POST'])
def gconnect():
    # Validate state token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Obtain authorization code
    code = request.data

    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(
            json.dumps('Failed to upgrade the authorization code.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Check that the access token is valid.
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
           % access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    # If there was an error in the access token info, abort.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is used for the intended user.
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app's."), 401)
        print "Token's client ID does not match app's."
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_access_token = login_session.get('access_token')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_access_token is not None and gplus_id == stored_gplus_id:
        response = make_response(
            json.dumps('Current user is already connected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store the access token in the session for later use.
    login_session['access_token'] = credentials.access_token
    login_session['gplus_id'] = gplus_id

    # Get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()

    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']

    print data['email']
    if session.query(User).filter_by(email=data['email']).count() != 0:
        current_user = session.query(User).filter_by(email=data['email']).one()
    else:
        newUser = User(name=data['name'],
                       email=data['email'])
        session.add(newUser)
        session.commit()
        current_user = newUser

    login_session['user_id'] = current_user.id
    print current_user.id

    output = ''
    output += login_session['username']
    output += login_session['picture']
    return output


@app.route('/gdisconnect')
def gdisconnect():
    access_token = login_session['access_token']
    print 'In gdisconnect access token is %s', access_token
    print 'User name is: '
    print login_session['username']
    if access_token is None:
        print 'Access Token is None'
        response = make_response(
            json.dumps('Current user not connected.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    url = ('https://accounts.google.com/o/oauth2/revoke?token=%s'
           % access_token)
    h = httplib2.Http()
    result = \
        h.request(uri=url, method='POST', body=None, headers={
                  'content-type': 'application/x-www-form-urlencoded'})[0]

    print url
    print 'result is '
    print result
    if result['status'] == '200':
        del login_session['access_token']
        del login_session['gplus_id']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']
        response = make_response(json.dumps('Successfully disconnected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        flash("Successfully logged out", "success")
        return redirect('/home')
        # return response
    else:
        response = make_response(
            json.dumps('Failed to revoke token for given user.', 400))
        response.headers['Content-Type'] = 'application/json'
        return response


# Show all restaurants
@app.route("/")
@app.route("/home")
def home():
    # Show all Restaurants
    restaurants = session.query(Restaurant).all()
    menus = session.query(MenuItem).all()
    return render_template('home.html', restaurants=restaurants, menus=menus)


# JSON Restaurants View
@app.route('/json')
def restaurantsJSON():
    restaurants = session.query(Restaurant).options(
        joinedload(Restaurant.items)).all()
    return jsonify(restaurants=[dict(c.serialize, items=[i.serialize
                                                         for i in c.items])
                                for c in restaurants])


# Create new restaurant
@app.route('/restaurant/new/', methods=['GET', 'POST'])
def createNewRestaurant():
    if 'username' not in login_session:
        return redirect('/login')
    user_id = login_session['user_id']
    if 'username' not in login_session:
        return redirect('/login')
    if request.method == 'POST':
        newRestaurant = Restaurant(name=request.form['restaurant_name'],
                                   user_id=user_id)
        session.add(newRestaurant)
        flash('Successfully created', "success")
        session.commit()
        return redirect(url_for('home'))
    else:
        return render_template('new_restaurant.html')


# Edit Restaurant
@app.route('/restaurant/<int:restaurant_id>/restaurant/edit',
           methods=['GET', 'POST'])
def editRestaurant(restaurant_id):
    # check if username is logged in
    if 'username' not in login_session:
        return redirect('/login')
    editRestaurant = session.query(Restaurant).filter_by(
        id=restaurant_id).one()
    if editRestaurant.user_id != login_session['user_id']:
        flash('Sorry, you are not allowed to edit', "danger")
        return redirect(url_for('home'))
    if request.method == 'POST':
        if request.form['restaurant_name']:
            editRestaurant.name = request.form['restaurant_name']
            session.add(editRestaurant)
            session.commit()
            flash('Successfully Edited', 'success')
            return redirect(url_for('home'))
    else:
        return render_template('edit_restaurant.html',
                               restaurant_id=restaurant_id,
                               editRestaurant=editRestaurant)


# Delete Restaurant
@app.route('/restaurant/<int:restaurant_id>/restaurant/delete',
           methods=['GET', 'POST'])
def deleteRestaurant(restaurant_id):
    # check if username is logged in
    if 'username' not in login_session:
        return redirect('/login')
    delRestaurant = session.query(Restaurant).filter_by(
        id=restaurant_id).one()
    if delRestaurant.user_id != login_session['user_id']:
        flash('Sorry, you are not allowed to Delete', "danger")
        return redirect(url_for('home'))
    if request.method == 'POST':
        session.delete(delRestaurant)
        session.commit()
        flash('Successfully Deleted', 'success')
        return redirect(url_for('home'))
    else:
        return render_template('delete_restaurant.html',
                               delRestaurant=delRestaurant)


# Selecting a specific Restaurant Menu
@app.route('/restaurant/<string:restaurant_name>/menu/')
def showspecificRestaurantMenu(restaurant_name):
    """Show all Items"""
    restaurants = session.query(Restaurant).all()
    specific_res_name = session.query(Restaurant).filter_by(
        name=restaurant_name).one()
    items = session.query(MenuItem).filter_by(
        restaurant_id=specific_res_name.id).all()
    countitem = session.query(MenuItem).filter_by(
        restaurant_id=specific_res_name.id).count()
    return render_template('specific_restaurant_menu.html',
                           items=items, restaurants=restaurants,
                           specific_res_name=specific_res_name,
                           countitem=countitem)

# Display Restaurant Menu
@app.route('/restaurant/<int:restaurant_id>/')
@app.route('/restaurant/<int:restaurant_id>/menu/')
def showMenuItem(restaurant_id):
    """Show all Items"""
    restaurant = session.query(Restaurant).filter_by(
        id=restaurant_id).one()
    items = session.query(MenuItem).filter_by(
        restaurant_id=restaurant_id).all()
    return render_template('menu_item.html', items=items,
                           restaurant=restaurant)

# Selecting a specific  Menu item
@app.route('/restaurant/<string:restaurant_name>/<string:menu_name>/')
def showSpecificMenuItem(restaurant_name, menu_name):
    """Show all Items"""
    restaurant = session.query(Restaurant).filter_by(
        name=restaurant_name).one()
    item = session.query(MenuItem).filter_by(
        name=menu_name).one()
    return render_template('specific_menu.html',
                           item=item, restaurant=restaurant)


# Create new restaurant
@app.route('/restaurant/menu/new', methods=['GET', 'POST'])
def createNewMenu():
    # check if username is logged in
    if 'username' not in login_session:
        return redirect('/login')
    user_id = login_session['user_id']
    allrestaurant = session.query(Restaurant).all()
    if request.method == 'POST':
        if request.form['menu_restaurant_id'] == "":
            flash('Please select a restaurant name.', "danger")
            return render_template('new_menu.html',
                                   allrestaurant=allrestaurant)
        restaurant = session.query(Restaurant).filter_by(
            id=request.form['menu_restaurant_id']).one()
        if restaurant.user_id != login_session['user_id']:
            flash('Sorry, you are not allowed to Add a menu', "danger")
            return redirect(url_for('home'))
        newMenu = MenuItem(name=request.form['menu_name'],
                           description=request.form['menu_description'],
                           price=request.form['menu_price'],
                           restaurant_id=request.form['menu_restaurant_id'],
                           user_id=user_id)
        session.add(newMenu)
        flash('Successfully created', 'success')
        session.commit()
        return redirect(url_for('showMenuItem',
                                restaurant_id=request.form['menu_restaurant_id'
                                                           ]))
    else:
        return render_template('new_menu.html', allrestaurant=allrestaurant)

# Edit exists menu
@app.route('/restaurant/<int:restaurant_id>/menu/<int:menu_id>/edit',
           methods=['GET', 'POST'])
def editMenu(restaurant_id, menu_id):
    # check if username is logged in
    if 'username' not in login_session:
        return redirect('/login')
    menuItem = session.query(MenuItem).filter_by(id=menu_id).one()
    if menuItem.user_id != login_session['user_id']:
        flash('Sorry, you are not allowed to edit', "danger")
        return redirect(url_for('showMenuItem', restaurant_id=restaurant_id))
    if request.method == 'POST':
        if request.form['menu_name']:
            menuItem.name = request.form['menu_name']
        if request.form['menu_description']:
            menuItem.description = request.form['menu_description']
        if request.form['menu_price']:
            menuItem.price = request.form['menu_price']
            session.add(menuItem)
            session.commit()
            flash('Successfully Edited', 'success')
            return redirect(url_for('showMenuItem',
                                    restaurant_id=restaurant_id))
    else:
        return render_template('edit_menu.html', restaurant_id=restaurant_id,
                               menu_id=menu_id, menuItem=menuItem)


# Delete exists menu
@app.route('/restaurant/<int:restaurant_id>/menu/<int:menu_id>/delete',
           methods=['GET', 'POST'])
def deleteMenu(restaurant_id, menu_id):

    # check if username is logged in
    if 'username' not in login_session:
        return redirect('/login')
    menuItem = session.query(MenuItem).filter_by(id=menu_id).one()
    if menuItem.user_id != login_session['user_id']:
        flash('Sorry, you are not allowed to delete', "danger")
        return redirect(url_for('showMenuItem', restaurant_id=restaurant_id))

    if request.method == 'POST':
        session.delete(menuItem)
        session.commit()
        flash('Successfully Deleted', 'success')
        return redirect(url_for('showMenuItem', restaurant_id=restaurant_id))
    else:
        return render_template('delete_menu.html',
                               restaurant_id=restaurant_id,
                               menu_id=menu_id,
                               menuItem=menuItem)


if __name__ == '__main__':
    app.config['SECRET_KEY'] = '6d22391e94319de409da02ff3f19dd83'
    app.debug = True
    app.run(host='0.0.0.0', port=8000)
