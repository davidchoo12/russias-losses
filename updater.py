from pathlib import Path
import logging
import requests
import json
# src https://towardsdatascience.com/read-text-from-image-with-one-line-of-python-code-c22ede074cac
import cv2
from datetime import date
import pytesseract
import numpy as np
from urllib.request import urlopen
import re
import os
import difflib

data_file_path = 'public/data.json'

Path('logs').mkdir(exist_ok=True)
log_format = '%(relativeCreated)8d %(message)s'
logging.basicConfig(level=logging.DEBUG, format=log_format)
file_handler = logging.FileHandler('logs/updater-facebook.txt', 'w')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(log_format))
logger = logging.getLogger()
logger.addHandler(file_handler)

##### OCR on facebook images posts #####

# src https://stackoverflow.com/a/55026951/4858751
def url_to_image(url, readFlag=cv2.IMREAD_GRAYSCALE):
  resp = urlopen(url)
  arr = np.asarray(bytearray(resp.read()), dtype="uint8")
  image = cv2.imdecode(arr, readFlag)
  image = cv2.threshold(image, 10, 255, cv2.THRESH_BINARY)[1]
  return image

# perc params range 0 - 1
def crop_img(img, upper_x_perc, upper_y_perc, lower_x_perc, lower_y_perc):
  height, width = img.shape
  upper_x = round(width * upper_x_perc)
  upper_y = round(height * upper_y_perc)
  lower_x = round(width * lower_x_perc)
  lower_y = round(height * lower_y_perc)
  return img[upper_y:lower_y, upper_x:lower_x]

def url_to_images(url):
  img = url_to_image(url)
  crop_coords = [[0.15, 0.21, 0.5, 0.95], [0.62, 0.22, 1, 1]]
  cropped_imgs = []
  for crop_coord in crop_coords:
    cropped = crop_img(img, crop_coord[0], crop_coord[1], crop_coord[2], crop_coord[3])
    cropped_imgs.append(cropped)
  return cropped_imgs

def text_to_stats(text):
  result = []
  rgx = re.compile(r'(\d[\d,.]*)[^0-9a-zA-Z]+(\D+?(?:\n[a-zA-Z ]+)*)\n')
  for match in rgx.finditer(text):
    count, unit = match.groups()
    count = int(count.replace(',', '').replace('.', ''))
    unit = unit.replace('\n', ' ')
    result.append((count, unit))
  return result

def image_to_stats(img):
  # src https://pyimagesearch.com/2021/11/15/tesseract-page-segmentation-modes-psms-explained-how-to-improve-your-ocr-accuracy/
  # psm 4: Assume a single column of text of variable sizes.
  text = pytesseract.image_to_string(img, lang='eng', config='--psm 4')
  logger.info(text)
  return text_to_stats(text)

def url_to_stats(url):
  imgs = url_to_images(url)
  count_unit_list = []
  for img in imgs:
    count_unit_list += image_to_stats(img)
  return count_unit_list

def pid_to_stats(pid):
  res = ses.get(base_url + f'/photo.php?fbid={pid}')
  save_res(res, f'russias-losses-updater-last-response.html')
  if not res.ok:
    return
  created_time_image_url_match = created_time_image_url_re.search(res.text)
  created_time = created_time_image_url_match.group(1)
  created_date = date.fromtimestamp(int(created_time)).isoformat()
  image_url = created_time_image_url_match.group(2)
  image_url = image_url.replace('\\', '')
  count_unit_list = url_to_stats(image_url)
  return (created_date, count_unit_list)

def pids_to_data(pids):
  data = {}
  date_url = {}
  for pid in pids:
    created_date, count_unit_list = pid_to_stats(pid)
    if created_date in date_url:
      logger.error('unexpected multiple posts on same date found in new posts, %s and https://fb.com/%s', date_url[created_date], pid)
      exit(1)
    data[created_date] = count_unit_list
    date_url[created_date] = f'https://fb.com/{pid}'
  return (data, date_url)

def print_data(data):
  for date, count_unit_list in data.items():
    logger.info('%s %s', date, ', '.join(' '.join(str(w) for w in t) for t in count_unit_list))


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
  known_units = 'troops,planes,helicopters,tanks,artillery system,armored personnel carriers,MLRS,boats,vehicles,fuel tanks,UAV,anti-aircraft warfare,special equipment,mobile SRBM systems,APV,boats / cutters,vehicles and fuel tanks,cruise missiles'.split(',')
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
    # unit change 11-11 ("artillery units" and "artillery pieces" were used previously)
    rename_unit(data[date], 'artillery units', 'artillery system')
    rename_unit(data[date], 'artillery pieces', 'artillery system')
    if date == '2022-11-11':
      data[date] = [(79400,'troops'),(5696,'APV'),(4259,'vehicles and fuel tanks'),(2814,'tanks'),(1817,'artillery system'),(1505,'UAV'),(393,'MLRS'),(278,'planes'),(261,'helicopters'),(399,'cruise missiles'),(205,'anti-aircraft warfare'),(159,'special equipment'),(16,'boats / cutters')] # src https://t.co/NnrzUkZVBD


##### Data wrangling #####

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

def merge_date_source(saved_date_source, new_date_source):
  for date, source in new_date_source.items():
    if date in saved_date_source:
      logger.error('unexpected new post %s on same date %s as existing post %s', source, date, saved_date_source[date])
      exit(1)
    saved_date_source[date] = source
  return saved_date_source

def save_processed_data(data, tweets_date_url):
  to_save = {
    'stats': data,
    'date_source': tweets_date_url,
  }
  with open(data_file_path, 'w') as f:
    json.dump(to_save, f, separators=(',', ':'))

def load_data_json():
  if not os.path.isfile(data_file_path):
    return {}
  with open(data_file_path, 'r') as f:
    data = json.load(f)
  return data

def get_latest_pid(saved_date_source):
  latest_date = max(saved_date_source.keys())
  latest_source = saved_date_source[latest_date]
  return latest_source.split('/')[-1]

def save_res(res, name):
  res_dest = f'/tmp/{name}'
  logger.error('saving response to ' + res_dest)
  with open(res_dest, 'w') as fd:
    fd.write(res.text)


##### Data scraping #####

ses = requests.Session()
base_url = 'https://www.facebook.com'
default_headers = {
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.5',
  'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/109.0',
  'Sec-Fetch-Site': 'none',
}
ses.headers.update(default_headers)

cursor_re = re.compile(r'"end_cursor":"([^"]+)","has_next_page":true')
post_ids_re = re.compile(r'"id":"([^"]+)","accessibility_caption":"(?:\\"|[^"])*Russia\'s losses')
created_time_image_url_re = re.compile(r'"created_time":(\d+),"image":{"uri":"([^"]+)"')

def scrape_new_pids(latest_pid):
  res = ses.get(base_url + '/kyivindependent/photos')
  save_res(res, f'russias-losses-updater-last-response.html')
  if not res.ok:
    return

  cursor = cursor_re.search(res.text).group(1)
  post_ids_matches = post_ids_re.findall(res.text)
  scraped_pids = curr_pids = list(dict.fromkeys(post_ids_matches))
  while latest_pid not in curr_pids:
    formdata = {
      'variables': json.dumps({
        'count': 8,
        'cursor': cursor,
        'id': 'YXBwX2NvbGxlY3Rpb246MTAwMDc1NzgyMzU2Mzk4OjIzMDUyNzI3MzI6NQ=='
      }, separators=(',', ':')),
      'doc_id': '6133315026790556',
    }
    res = ses.post(base_url + '/api/graphql', data=formdata)
    save_res(res, f'russias-losses-updater-last-response.html')
    if not res.ok:
      return
    curr_pids = list(dict.fromkeys(post_ids_re.findall(res.text)))
    cursor = cursor_re.search(res.text).group(1)
    scraped_pids.extend(curr_pids)
  for i, pid in enumerate(scraped_pids):
    if pid == latest_pid:
      scraped_pids = scraped_pids[:i]
      break
  scraped_pids.reverse()
  return scraped_pids


if __name__ == '__main__':
  saved_data = load_data_json()
  saved_date_source = saved_data['date_source']
  latest_pid = get_latest_pid(saved_date_source)

  new_pids = scrape_new_pids(latest_pid)
  new_data, new_date_source = pids_to_data(new_pids)
  if len(new_data) == 0:
    logger.info('No new data')
    exit(0)
  logger.info('New data:')
  print_data(new_data)

  saved_processed_data = saved_data['stats']
  all_processed_data = process_data(new_data, saved_data_per_unit=saved_processed_data)
  # logger.info('All processed data:')
  # print_data(all_processed_data)

  all_date_source = merge_date_source(saved_date_source, new_date_source)

  save_processed_data(all_processed_data, all_date_source)