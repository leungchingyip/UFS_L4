App Engine application for the Udacity training course.
Full stack nanodegree project 4.


## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool


## Task 1

Following the construction of Conference and ConferenceForm, I set the date and time in model Session as Date or Time Property, and messages SessionForm in StringField.

I found DateTimeField in protorpc.message_types when I read the official document later. It uses the instance method to do conversions bewteen datetime type and DataTimeField. It may make the code looks more tidy than did it by timestrp() and str() I think. 

I set the Session as a child of Conference. It is easy to get each other by ndb.Key.

## Task 3

### getConferenceAndSessionByDate

When user are free on one day and want to join a workshop or have a lecture. User can just search conference and session on the day derectly.

### getConferenceAndSessionByKeyword

When user are insteresting in some theme specificly and want to search will there be a conference about it. User can use keyword to search. The search area includes name, description, topics property of conference, sessionName, hightlights and conferenceBelongTo property of session.

### Problem solve.

The problem we have is that google cloud platform query only support one inequality filter per query.

I think there are two ways to solve it.

1. I think the number of typeOfSession will be quite limited. For example, if there are just three choices in typeOfSessoin(lecture, keynote, workshop). So when user don't want workshop. It means user want lecture or keynote. So we can use Sesssion.typeOfSession.IN("lecture", "keynote") instead of filter(Sesssion.typeOfSession != "workshop").

2. We still use filter(Sesssion.typeOfSession != "workshop"). When we got the none workshop query. We order it by startTime. And get it one by one by for loop. Then check if each object's startTime greater than 19:00. If true, put it in a list. When the for loop finished, the list is the query we want.  

For this example, I think first way is more efficient. I choose first way to implement it.

