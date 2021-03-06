import json
import tweepy
# src https://towardsdatascience.com/read-text-from-image-with-one-line-of-python-code-c22ede074cac
import cv2
from datetime import date
import pytesseract
import numpy as np
from urllib.request import urlopen
import re
from concurrent.futures import ProcessPoolExecutor
import os
import difflib

bearer_token = os.environ.get('TWITTER_TOKEN')
auth = tweepy.OAuth2BearerHandler(bearer_token)
api = tweepy.API(auth)

tweets_file_path = 'public/tweets.json'
data_file_path = 'public/data.json'


##### Scraping tweets #####

def load_saved_tweets():
  if not os.path.isfile(tweets_file_path):
    return []
  with open(tweets_file_path, 'r') as f:
    saved_tweets = json.load(f)
  saved_tweets = [tweepy.models.Status.parse(api, t) for t in saved_tweets]
  return saved_tweets

def print_tweets(tweets):
  for tweet in tweets:
    start_index, end_index = tweet.display_text_range
    text = tweet.full_text[start_index:end_index]
    url = tweet.full_text[end_index+1:]
    print(tweet.id, url, tweet.entities['media'][0]['media_url_https'], text)

def get_new_tweets(saved_tweets):
  new_tweets = []
  for tweet in tweepy.Cursor(api.user_timeline, screen_name='KyivIndependent', tweet_mode='extended').items(2000):
    # if found last saved tweet, break
    if len(saved_tweets) > 0 and tweet.id == saved_tweets[-1].id:
      break
    if 'media' in tweet.entities and 'losses' in tweet.full_text:
      new_tweets.append(tweet)
  return new_tweets

def save_tweets(tweets):
  with open(tweets_file_path, 'w') as f:
    json.dump([t._json for t in tweets], f, separators=(',', ':'))
  return

def get_tweets_date_url(tweets):
  date_url = {}
  for tweet in tweets:
    date = tweet.created_at.date().isoformat()
    start_index, end_index = tweet.display_text_range
    text = tweet.full_text[start_index:end_index]
    url = tweet.full_text[end_index+1:]
    date_url[date] = url
  return date_url


##### OCR on tweets' images #####

# src https://stackoverflow.com/a/55026951/4858751
def url_to_image(url, readFlag=cv2.IMREAD_COLOR):
  resp = urlopen(url)
  arr = np.asarray(bytearray(resp.read()), dtype="uint8")
  image = cv2.imdecode(arr, readFlag)
  return image

# perc params range 0 - 1
def crop_img(img, upper_x_perc, upper_y_perc, lower_x_perc, lower_y_perc):
  height, width, _ = img.shape
  upper_x = round(width * upper_x_perc)
  upper_y = round(height * upper_y_perc)
  lower_x = round(width * lower_x_perc)
  lower_y = round(height * lower_y_perc)
  return img[upper_y:lower_y, upper_x:lower_x]

def tweet_to_images(tweet):
  img_url = tweet.entities['media'][0]['media_url_https']
  img = url_to_image(img_url)
  crop_coords = [[0.14, 0.22, 0.5, 0.9], [0.6, 0.22, 1, 1]]
  if tweet.created_at.date() > date(2022, 5, 1):
    crop_coords = [[0.13, 0.22, 0.5, 0.9], [0.58, 0.22, 1, 1]]
  cropped_imgs = []
  for crop_coord in crop_coords:
    cropped = crop_img(img, crop_coord[0], crop_coord[1], crop_coord[2], crop_coord[3])
    cropped_imgs.append(cropped)
  return cropped_imgs

def text_to_stats(text):
  result = []
  rgx = re.compile(r'(\d[\d,]*)[^0-9a-zA-Z]+(\D+?(?:\n[a-zA-Z ]+)*)\n')
  for match in rgx.finditer(text):
    count, unit = match.groups()
    count = int(count.replace(',', ''))
    unit = unit.replace('\n', ' ')
    result.append((count, unit))
  return result

def image_to_stats(img):
  # src https://pyimagesearch.com/2021/11/15/tesseract-page-segmentation-modes-psms-explained-how-to-improve-your-ocr-accuracy/
  # psm 4: Assume a single column of text of variable sizes.
  text = pytesseract.image_to_string(img, lang='eng', config='--psm 4')
  return text_to_stats(text)

def tweet_to_stats(tweet):
  imgs = tweet_to_images(tweet)
  count_unit_list = []
  for img in imgs:
    count_unit_list += image_to_stats(img)
  return count_unit_list

def tweets_to_data(tweets):
  data = {}
  with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
    for tweet, count_unit_list in zip(tweets, executor.map(tweet_to_stats, tweets)):
      count_unit_list = tweet_to_stats(tweet)
      data[tweet.created_at.date().isoformat()] = count_unit_list
  return data

def print_data(data):
  for date, count_unit_list in data.items():
    print(date, ', '.join(' '.join(str(w) for w in t) for t in count_unit_list))


##### Data cleanup #####

# to fix OCR approximate matches, eg alanes should be planes
def fix_approximate_units(count_unit_list, known_units):
  for i, [count, unit] in enumerate(count_unit_list):
    close_matches = difflib.get_close_matches(unit, known_units)
    if len(close_matches) > 0:
      count_unit_list[i] = [count, close_matches[0]]
  return count_unit_list

def override_unit_count(count_unit_list, target_unit, new_count):
  for i, [count, unit] in enumerate(count_unit_list):
    if unit == target_unit:
      count_unit_list[i] = [new_count, unit]
  return count_unit_list

# for changes in unit names on subsequent images
def rename_unit(count_unit_list, from_unit, to_unit):
  for i, [count, unit] in enumerate(count_unit_list):
    if unit == from_unit:
      count_unit_list[i] = [count, to_unit]
  return count_unit_list

# for merging of unit names on subsequent images
def merge_units(count_unit_list, to_unit, merger_lambda, *from_units):
  # skip if to_unit alr exists
  for count, unit in count_unit_list:
    if unit == to_unit:
      return
  # get counts of from_units
  from_counts = {}
  i = 0
  while i < len(count_unit_list):
    count, unit = count_unit_list[i]
    if unit in from_units:
      from_counts[unit] = count
      count_unit_list.pop(i)
    else:
      i += 1
  count = merger_lambda(*[from_counts.get(unit, 0) for unit in from_units])
  count_unit_list.append((count, to_unit))

def clean_data(data):
  known_units = 'troops,planes,helicopters,tanks,artillery pieces,armored personnel carriers,MLRS,boats,vehicles,fuel tanks,UAV,anti-aircraft warfare,special equipment,mobile SRBM systems,APV,boats / cutters,vehicles and fuel tanks,cruise missiles'.split(',')
  for date in data:
    fix_approximate_units(data[date], known_units)
    if date == '2022-02-26':
      data[date] = [(3500,'troops'),(14,'planes'),(8,'helicopters'),(102,'tanks'),(15,'artillery pieces'),(536,'armored personnel carriers'),(1,'BUK system')]
    elif date == '2022-03-26':
      override_unit_count(data[date], 'special equipment', 19) # src https://t.co/z6plV7HBNa
    rename_unit(data[date], 'cars', 'vehicles')
    rename_unit(data[date], 'drones', 'UAV')
    rename_unit(data[date], 'ships/patrol boats', 'boats')
    rename_unit(data[date], 'Hgeps', 'troops') # 2022-04-15 "troops" detected as "Hgeps" on my laptop
    rename_unit(data[date], 'Hees', 'troops') # 2022-04-15 "troops" detected as "Hees" on github actions
    merge_units(data[date], 'MLRS', lambda buk, grad: buk+grad, 'BUK system', 'Grad systems')
    # units changes between 05-01 - 05-02
    rename_unit(data[date], 'armored personnel carriers', 'APV')
    rename_unit(data[date], 'boats', 'boats / cutters')
    merge_units(data[date], 'vehicles and fuel tanks', lambda vehicles, fuel_tanks: vehicles+fuel_tanks, 'vehicles', 'fuel tanks')
    if date == '2022-05-01':
      override_unit_count(data[date], 'vehicles and fuel tanks', 1796) # src https://t.co/yX3LtMGXtA

def sort_units(data_per_unit, units):
  sorted_data_per_unit = {}
  for unit in units:
    sorted_data_per_unit[unit] = data_per_unit[unit]
    del data_per_unit[unit]
  # append any remaining data of units that are not in the latest data
  sorted_data_per_unit |= data_per_unit
  return sorted_data_per_unit

def transpose_data(data, saved_data_per_unit={}):
  for date, count_unit_list in data.items():
    for count, unit in count_unit_list:
      if all(date != saved_date for saved_date, saved_count in saved_data_per_unit.get(unit, [])):
        saved_data_per_unit[unit] = saved_data_per_unit.get(unit, []) + [[date, count]]
  if len(data) > 1:
    latest_date = sorted(data.keys())[-1]
    unit_sequence = [unit for [count, unit] in data[latest_date]]
    saved_data_per_unit = sort_units(saved_data_per_unit, unit_sequence)
  return saved_data_per_unit

def process_data(data, saved_data_per_unit={}):
  clean_data(data)
  data_per_unit = transpose_data(data, saved_data_per_unit)
  return data_per_unit

def save_processed_data(data, tweets_date_url):
  to_save = {
    'stats': data,
    'date_source': tweets_date_url,
  }
  with open(data_file_path, 'w') as f:
    json.dump(to_save, f, separators=(',', ':'))

def load_processed_data():
  if not os.path.isfile(data_file_path):
    return {}
  with open(data_file_path, 'r') as f:
    data = json.load(f)
  return data.get('stats', {})


if __name__ == '__main__':
  saved_tweets = load_saved_tweets()
  new_tweets = get_new_tweets(saved_tweets)[::-1]
  print('New tweets:')
  print_tweets(new_tweets)

  all_tweets = saved_tweets + new_tweets
  # all_tweets = all_tweets[:-3]
  save_tweets(all_tweets)
  all_tweets_date_url = get_tweets_date_url(all_tweets)

  new_data = tweets_to_data(new_tweets)
  print('New data:')
  print_data(new_data)

  saved_processed_data = load_processed_data()
  all_processed_data = process_data(new_data, saved_data_per_unit=saved_processed_data)
  print('All processed data:')
  print_data(all_processed_data)

  save_processed_data(all_processed_data, all_tweets_date_url)