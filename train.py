#! /usr/bin/env python

"""
This script takes in a configuration file and produces the best model. 
The configuration file is a json file and looks like this:

{
    "model" : {
        "architecture": "Full Yolo",
        "input_size": 416,
        "anchors": [0.57273, 0.677385, 1.87446, 2.06253, 3.33843, 5.47434, 7.88282, 3.52778, 9.77052, 9.16828],
        "max_box_per_image": 20,        
        "labels": ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"]
    },

    "train": {
        "train_image_folder": "/home/andy/data/raccoon_dataset/images/",
        "train_annot_folder": "/home/andy/data/raccoon_dataset/anns/",      
          
        "pretrained_weights": "",
        "batch_size": 2,
        "learning_rate": 1e-4,
        "nb_epoch": 30,
        "warmup_batches": 10000
    },

    "valid": {
        "valid_image_folder": "",
        "valid_annot_folder": ""
    }
}

The first 5 parameters are compulsory. Their names are self-explanatory.

The rest of the parameters can be left to the defaults.
"""

import argparse
import os
import numpy as np
from preprocessing import parse_annotation
from models import YOLO
import json

os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"]="7"

argparser = argparse.ArgumentParser(
    description='Train and validate YOLO_v2 model on any dataset')

argparser.add_argument(
    '-c',
    '--conf',
    help='path to configuration file')

def _main_(args):

    config_path = args.conf

    with open(config_path) as config_buffer:    
        config = json.load(config_buffer)

    ###############################
    #   Parse the annotations 
    ###############################

    # parse annotations of the training set
    train_imgs, train_labels = parse_annotation(config['train']['train_annot_folder'], 
                                                config['train']['train_image_folder'], 
                                                config['model']['labels'])

    # parse annotations of the validation set, if any, otherwise split the training set
    if os.path.exists(config['valid']['valid_annot_folder']):
        valid_imgs, valid_labels = parse_annotation(config['valid']['valid_annot_folder'], 
                                                    config['valid']['valid_image_folder'], 
                                                    config['model']['labels'])
    else:
        train_valid_split = int(0.9*len(train_imgs))

        valid_imgs = train_imgs[:train_valid_split]
        train_imgs = train_imgs[train_valid_split:]

    ###############################
    #   Construct the model 
    ###############################

    yolo = YOLO(architecture=config['model']['architecture'],
                input_size=config['model']['input_size'], 
                labels=config['model']['labels'], 
                max_box_per_image=config['model']['max_box_per_image'],
                anchors=config['model']['anchors'])

    ###############################
    #   Load the pretrained weights (if any) 
    ###############################    

    if os.path.exists(config['train']['pretrained_weights']):
        yolo.load_weights(config['train']['pretrained_weights'])

    ###############################
    #   Start the training process 
    ###############################

    yolo.train(train_imgs, 
               valid_imgs, 
               config['train']['nb_epoch'], 
               config['train']['learning_rate'], 
               config['train']['batch_size'],
               config['train']['warmup_batches'])

if __name__ == '__main__':
    args = argparser.parse_args()
    _main_(args)