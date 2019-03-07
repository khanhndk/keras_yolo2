from .yolo_loss import YoloLoss
from .utils import decode_netout, compute_overlap, compute_ap, import_feature_extractor
from .preprocessing import BatchGenerator
from keras.models import Model
from keras.layers import Reshape, Conv2D, Input
from keras.optimizers import Adam
from keras.callbacks import EarlyStopping, ModelCheckpoint, TensorBoard
import numpy as np
import keras
import cv2
import os


class YOLO(object):
    def __init__(self, backend,
                       input_size, 
                       labels, 
                       max_box_per_image,
                       anchors,
                       gray_mode = False):

        self.input_size = input_size
        self.gray_mode  = gray_mode
        
        self.labels   = list(labels)
        self.nb_class = len(self.labels)
        self.nb_box   = len(anchors)//2
        self.class_wt = np.ones(self.nb_class, dtype='float32')
        self.anchors  = anchors

        self.max_box_per_image = max_box_per_image

        ##########################
        # Make the model
        ##########################

        # make the feature extractor layers
        if self.gray_mode:
            self.input_size = (self.input_size[0], self.input_size[1], 1)
            input_image     = Input(shape=self.input_size)
        else:
            self.input_size = (self.input_size[0], self.input_size[1], 3)
            input_image     = Input(shape=self.input_size)

        self.feature_extractor = import_feature_extractor(backend, self.input_size)
        
        print(self.feature_extractor.get_output_shape())    
        self.grid_h, self.grid_w = self.feature_extractor.get_output_shape()        
        features = self.feature_extractor.extract(input_image)          

        # make the object detection layer
        output = Conv2D(self.nb_box * (4 + 1 + self.nb_class), 
                        (1,1), strides=(1,1), 
                        padding='same', 
                        name='Detection_layer', 
                        kernel_initializer='lecun_normal')(features)
        output = Reshape((self.grid_h, self.grid_w, self.nb_box, 4 + 1 + self.nb_class),name="YOLO_output")(output)

        self.model = Model(input_image, output)
       
        # initialize the weights of the detection layer
        layer = self.model.get_layer("Detection_layer")
        weights = layer.get_weights()

        new_kernel = np.random.normal(size=weights[0].shape)/(self.grid_h*self.grid_w)
        new_bias   = np.random.normal(size=weights[1].shape)/(self.grid_h*self.grid_w)

        layer.set_weights([new_kernel, new_bias])

        # print a summary of the whole model
        self.model.summary()

    def load_weights(self, weight_path):
        self.model.load_weights(weight_path)

    def train(self, train_imgs,     # the list of images to train the model
                    valid_imgs,     # the list of images used to validate the model
                    train_times,    # the number of time to repeat the training set, often used for small datasets
                    valid_times,    # the number of times to repeat the validation set, often used for small datasets
                    nb_epochs,      # number of epoches
                    learning_rate,  # the learning rate
                    batch_size,     # the size of the batch
                    warmup_epochs,  # number of initial batches to let the model familiarize with the new dataset
                    object_scale,
                    no_object_scale,
                    coord_scale,
                    class_scale,
                    saved_weights_name='best_weights.h5',
                    debug=False,
                    workers=3,
                    max_queue_size=8,
                    early_stop=True,
                    custom_callback=[],
                    tb_logdir="./"):     

        self.batch_size = batch_size

        self.object_scale    = object_scale
        self.no_object_scale = no_object_scale
        self.coord_scale     = coord_scale
        self.class_scale     = class_scale

        self.debug = debug

        ############################################
        # Make train and validation generators
        ############################################

        generator_config = {
            'IMAGE_H'         : self.input_size[0], 
            'IMAGE_W'         : self.input_size[1],
            'IMAGE_C'         : self.input_size[2],
            'GRID_H'          : self.grid_h,  
            'GRID_W'          : self.grid_w,
            'BOX'             : self.nb_box,
            'LABELS'          : self.labels,
            'CLASS'           : len(self.labels),
            'ANCHORS'         : self.anchors,
            'BATCH_SIZE'      : self.batch_size,
            'TRUE_BOX_BUFFER' : self.max_box_per_image,
        }    

        train_generator = BatchGenerator(train_imgs, 
                                     generator_config, 
                                     norm=self.feature_extractor.normalize)
        valid_generator = BatchGenerator(valid_imgs, 
                                     generator_config, 
                                     norm=self.feature_extractor.normalize,
                                     jitter=False)   
                                     
        self.warmup_batches  = warmup_epochs * (train_times*len(train_generator) + valid_times*len(valid_generator))   

        ############################################
        # Compile the model
        ############################################

        optimizer = Adam(lr=learning_rate, beta_1=0.9, beta_2=0.999, epsilon=1e-08, decay=0.0)
        loss_yolo = YoloLoss(self.anchors, (self.grid_w, self.grid_h), self.batch_size,
                            lambda_coord=coord_scale, lambda_noobj=no_object_scale, lambda_obj=object_scale,
                            lambda_class=class_scale)
        self.model.compile(loss=loss_yolo, optimizer=optimizer)

        ############################################
        # Make a few callbacks
        ############################################

        early_stop_cb = EarlyStopping(monitor='val_loss', 
                           min_delta=0.001, 
                           patience=3, 
                           mode='min', 
                           verbose=1)
        
        tensorboard_cb = TensorBoard(log_dir=tb_logdir, 
                                  histogram_freq=0, 
                                  #write_batch_performance=True,
                                  write_graph=True, 
                                  write_images=False)

        root, ext = os.path.splitext(saved_weights_name)
        ckp_best_loss = ModelCheckpoint(root+"_bestLoss"+ext, 
                                     monitor='val_loss', 
                                     verbose=1, 
                                     save_best_only=True, 
                                     mode='min', 
                                     period=1)
        ckp_saver = ModelCheckpoint(root+"_ckp"+ext, 
                                     verbose=1, 
                                     period=10)
        map_evaluator_cb = self.MAP_evaluation(self, valid_generator,
                                                save_best=True,
                                                save_name=root+"_bestMap"+ext,
                                                tensorboard=tensorboard_cb,
                                                iou_threshold=0.5)

        if not isinstance(custom_callback,list):
            custom_callback = [custom_callback]
        callbacks = [ckp_best_loss, ckp_saver,tensorboard_cb, map_evaluator_cb] + custom_callback
        if early_stop: callbacks.append(early_stop_cb)
        
        ############################################
        # Start the training process
        ############################################        

        self.model.fit_generator(generator        = train_generator, 
                                 steps_per_epoch  = len(train_generator) * train_times, 
                                 epochs           = warmup_epochs + nb_epochs, 
                                 verbose          = 2 if debug else 1,
                                 validation_data  = valid_generator,
                                 validation_steps = len(valid_generator) * valid_times,
                                 callbacks        = callbacks, 
                                 workers          = workers,
                                 max_queue_size   = max_queue_size)      
       

    def get_inference_model(self):
        return self.model

    def predict(self, image):

        if len(image.shape) == 3 and self.gray_mode:
            if image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                image = image[:,:,np.newaxis]
        elif len(image.shape) == 2 and not self.gray_mode:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif len(image.shape) == 2:
            image = image[:,:,np.newaxis]

        image = cv2.resize(image, (self.input_size[1], self.input_size[0]))
        image = self.feature_extractor.normalize(image)
        if len(image.shape) == 3:
            input_image = image[:,:,::-1]
            input_image = image[np.newaxis,:]
        else:
            input_image = image[np.newaxis,:,:,np.newaxis]
        
        netout = self.model.predict(input_image)[0]
            
        boxes  = decode_netout(netout, self.anchors, self.nb_class)

        return boxes

    class MAP_evaluation(keras.callbacks.Callback):
        """ Evaluate a given dataset using a given model.
            code originally from https://github.com/fizyr/keras-retinanet

            # Arguments
                generator       : The generator that represents the dataset to evaluate.
                model           : The model to evaluate.
                iou_threshold   : The threshold used to consider when a detection is positive or negative.
                score_threshold : The score confidence threshold to use for detections.
                save_path       : The path to save images with visualized detections to.
            # Returns
                A dict mapping class names to mAP scores.
        """   
        def __init__(self,
                    yolo,
                    generator, 
                    iou_threshold=0.5,
                    score_threshold=0.3,
                    save_path=None,
                    period=1,
                    save_best=False,
                    save_name=None,
                    tensorboard=None):
            
            self.yolo = yolo
            self.generator = generator
            self.iou_threshold = iou_threshold
            self.save_path = save_path
            self.period = period
            self.save_best = save_best
            self.save_name = save_name
            self.tensorboard = tensorboard

            self.bestMap = 0

            self.model = self.yolo.model

            if not isinstance(self.tensorboard,keras.callbacks.TensorBoard) and self.tensorboard is not None:
                raise ValueError("Tensorboard object must be a instance from keras.callbacks.TensorBoard")


        def on_epoch_end(self, epoch, logs={}):

            if epoch % self.period == 0 and self.period != 0:
                mAP, average_precisions = self.evaluate_mAP()
                print('\n')
                for label, average_precision in average_precisions.items():
                    print(self.yolo.labels[label], '{:.4f}'.format(average_precision))
                print('mAP: {:.4f}'.format(mAP)) 

                if self.save_best and self.save_name is not None and mAP > self.bestMap:
                    print("mAP improved from {} to {}, saving model to {}.".format(self.bestMap,mAP,self.save_name))
                    self.bestMap = mAP
                    self.model.save(self.save_name)
                else:
                    print("mAP did not improve from {}.".format(self.bestMap))

                if self.tensorboard is not None and self.tensorboard.writer is not None:
                    import tensorflow as tf
                    summary = tf.Summary()
                    summary_value = summary.value.add()
                    summary_value.simple_value = mAP
                    summary_value.tag = "val_mAP"
                    self.tensorboard.writer.add_summary(summary, epoch)

        def evaluate_mAP(self):
            average_precisions = self._calc_avg_precisions()
            mAP = sum(average_precisions.values()) / len(average_precisions)

            return mAP, average_precisions

        def _calc_avg_precisions(self):
             
            # gather all detections and annotations
            all_detections     = [[None for i in range(self.generator.num_classes())] for j in range(self.generator.size())]
            all_annotations    = [[None for i in range(self.generator.num_classes())] for j in range(self.generator.size())]

            for i in range(self.generator.size()):
                raw_image = self.generator.load_image(i)
                raw_height, raw_width, _ = raw_image.shape

                # make the boxes and the labels
                pred_boxes  = self.yolo.predict(raw_image)
                
                score = np.array([box.score for box in pred_boxes])
                pred_labels = np.array([box.label for box in pred_boxes])        
                
                if len(pred_boxes) > 0:
                    pred_boxes = np.array([[box.xmin*raw_width, box.ymin*raw_height, box.xmax*raw_width, box.ymax*raw_height, box.score] for box in pred_boxes])
                else:
                    pred_boxes = np.array([[]])  
                
                # sort the boxes and the labels according to scores
                score_sort = np.argsort(-score)
                pred_labels = pred_labels[score_sort]
                pred_boxes  = pred_boxes[score_sort]
                
                # copy detections to all_detections
                for label in range(self.generator.num_classes()):
                    all_detections[i][label] = pred_boxes[pred_labels == label, :]
                    
                annotations = self.generator.load_annotation(i)
                
                # copy detections to all_annotations
                for label in range(self.generator.num_classes()):
                    all_annotations[i][label] = annotations[annotations[:, 4] == label, :4].copy()
                    
            # compute mAP by comparing all detections and all annotations
            average_precisions = {}
            
            for label in range(self.generator.num_classes()):
                false_positives = np.zeros((0,))
                true_positives  = np.zeros((0,))
                scores          = np.zeros((0,))
                num_annotations = 0.0

                for i in range(self.generator.size()):
                    detections           = all_detections[i][label]
                    annotations          = all_annotations[i][label]
                    num_annotations     += annotations.shape[0]
                    detected_annotations = []

                    for d in detections:
                        scores = np.append(scores, d[4])

                        if annotations.shape[0] == 0:
                            false_positives = np.append(false_positives, 1)
                            true_positives  = np.append(true_positives, 0)
                            continue

                        overlaps            = compute_overlap(np.expand_dims(d, axis=0), annotations)
                        assigned_annotation = np.argmax(overlaps, axis=1)
                        max_overlap         = overlaps[0, assigned_annotation]

                        if max_overlap >= self.iou_threshold and assigned_annotation not in detected_annotations:
                            false_positives = np.append(false_positives, 0)
                            true_positives  = np.append(true_positives, 1)
                            detected_annotations.append(assigned_annotation)
                        else:
                            false_positives = np.append(false_positives, 1)
                            true_positives  = np.append(true_positives, 0)

                # no annotations -> AP for this class is 0 (is this correct?)
                if num_annotations == 0:
                    average_precisions[label] = 0
                    continue

                # sort by score
                indices         = np.argsort(-scores)
                false_positives = false_positives[indices]
                true_positives  = true_positives[indices]

                # compute false positives and true positives
                false_positives = np.cumsum(false_positives)
                true_positives  = np.cumsum(true_positives)

                # compute recall and precision
                recall    = true_positives / num_annotations
                precision = true_positives / np.maximum(true_positives + false_positives, np.finfo(np.float64).eps)

                # compute average precision
                average_precision  = compute_ap(recall, precision)  
                average_precisions[label] = average_precision

            return average_precisions    