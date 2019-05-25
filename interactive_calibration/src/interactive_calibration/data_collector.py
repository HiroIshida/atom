#!/usr/bin/env python

# ------------------------
#    IMPORT MODULES      #
# ------------------------
import copy
import json
import os
import shutil
from colorama import Style, Fore
import numpy as np
import cv2
import tf
from cv_bridge import CvBridge
from interactive_markers.menu_handler import *
# from sensor_msgs.msg import CameraInfo
from rospy_message_converter import message_converter
# to make sure that this expression works for most sensor msgs: msg = # rospy.wait_for_message(sensor['topic'],
# eval(sensor['msg_type']))
from sensor_msgs.msg import *

from visualization_msgs.msg import *
from tf.listener import TransformListener
from transformation_t import TransformationT
from urdf_parser_py.urdf import URDF


# ------------------------
#      BASE CLASSES      #
# ------------------------

# return Fore.GREEN + self.parent + Style.RESET_ALL + ' to ' + Fore.GREEN + self.child + Style.RESET_ALL + ' (' + self.joint_type + ')'

class DataCollector:

    def __init__(self, world_link, output_folder):

        if os.path.exists(output_folder):
            shutil.rmtree(output_folder)  # Delete old folder

        os.mkdir(output_folder)  # Create the new folder
        self.output_folder = output_folder

        self.listener = TransformListener()
        self.sensors = []
        self.world_link = world_link
        self.transforms = {}
        self.data_stamp = 0
        self.data = []
        self.bridge = CvBridge()
        rospy.sleep(0.5)

        # Parse robot description from param /robot_description
        xml_robot = URDF.from_parameter_server()

        # Add sensors
        print(Fore.BLUE + 'Sensors:' + Style.RESET_ALL)
        for i, xs in enumerate(xml_robot.sensors):
            self.assertXMLSensorAttributes(xs)  # raises exception if not ok

            # Create a dictionary that describes this sensor
            sensor_dict = {'_name': xs.name, 'parent': xs.parent, 'calibration_parent': xs.calibration_parent,
                           'calibration_child': xs.calibration_child}

            # Wait for a message to infer the type
            # http://schulz-m.github.io/2016/07/18/rospy-subscribe-to-any-msg-type/
            msg = rospy.wait_for_message(xs.topic, rospy.AnyMsg)
            connection_header = msg._connection_header['type'].split('/')
            ros_pkg = connection_header[0] + '.msg'
            msg_type = connection_header[1]
            print('Topic ' + xs.topic + ' has type ' + msg_type)
            sensor_dict['topic'] = xs.topic
            sensor_dict['msg_type'] = msg_type

            # If topic contains a message type then get a camera_info message to store along with the sensor data
            if sensor_dict['msg_type'] == 'Image':  # if it is an image must get camera_info
                sensor_dict['camera_info_topic'] = os.path.dirname(sensor_dict['topic']) + '/camera_info'
                from sensor_msgs.msg import CameraInfo
                camera_info_msg = rospy.wait_for_message(sensor_dict['camera_info_topic'], CameraInfo)
                from rospy_message_converter import message_converter
                sensor_dict['camera_info'] = message_converter.convert_ros_message_to_dictionary(camera_info_msg)

            # Get the kinematic chain form world_link to this sensor's parent link
            chain = self.listener.chain(xs.parent, rospy.Time(), self.world_link, rospy.Time(), self.world_link)
            chain_list = []
            for parent, child in zip(chain[0::], chain[1::]):
                key = self.generateKey(parent, child)
                chain_list.append({'key': key, 'parent': parent, 'child': child})

            sensor_dict['chain'] = chain_list  # Add to sensor dictionary
            self.sensors.append(sensor_dict)

            print(Fore.BLUE + xs.name + Style.RESET_ALL + ':\n' + str(sensor_dict))

    def collectSnapshot(self):

        # Collect transforms (for now collect all transforms even if they are fixed)
        abstract_transforms = self.getAllTransforms()
        transforms_dict = {}  # Initialize an empty dictionary that will store all the transforms for this data-stamp

        for ab in abstract_transforms:  # Update all transformations
            print(ab)
            self.listener.waitForTransform(ab['parent'], ab['child'], rospy.Time(), rospy.Duration(1.0))
            (trans, quat) = self.listener.lookupTransform(ab['parent'], ab['child'], rospy.Time())
            key = self.generateKey(ab['parent'], ab['child'])
            transforms_dict[key] = {'trans': trans, 'quat': quat}

        self.transforms[self.data_stamp] = transforms_dict

        # Collect sensor data (images, laser scans, etc)
        all_sensors_dict = {}
        for sensor in self.sensors:

            # TODO add exception also for point cloud and depht image
            if sensor['msg_type'] == 'Image':  #
                # Get latest ros message on this topic
                msg = rospy.wait_for_message(sensor['topic'], Image)

                # Convert to opencv image and save image to disk
                cv_image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
                filename = self.output_folder + '/' + sensor['_name'] + '_' + str(self.data_stamp) + '.jpg'
                filename_relative = sensor['_name'] + '_' + str(self.data_stamp) + '.jpg'
                print('Data ' + str(self.data_stamp) + ' from sensor ' + sensor['_name'] + ': saving image ' + filename)
                cv2.imwrite(filename, cv_image)
                # cv2.imshow('sensor', cv_image)
                # cv2.waitKey(0)

                # Convert the image to python dictionary
                image_dict = message_converter.convert_ros_message_to_dictionary(msg)

                # Remove data field (which contains the image), and replace by "data_file" field which contains the
                # full path to where the image was saved
                del image_dict['data']
                image_dict['data_file'] = filename_relative

                # Update the data dictionary for this data stamp
                all_sensors_dict[sensor['_name']] = image_dict

            else:
                # Get latest ros message on this topic
                # msg = rospy.wait_for_message(sensor['topic'], LaserScan)
                msg = rospy.wait_for_message(sensor['topic'], eval(sensor['msg_type']))

                # Update the data dictionary for this data stamp
                all_sensors_dict[sensor['_name']] = message_converter.convert_ros_message_to_dictionary(msg)

        # self.data[self.data_stamp] = all_sensors_dict
        self.data.append(all_sensors_dict)

        self.data_stamp += 1

        # Save to json file
        D = {'sensors': self.sensors, 'transforms': self.transforms, 'data': self.data}
        self.createJSONFile(self.output_folder + '/data_collected.json', D)

    def getAllTransforms(self):

        # Get a list of all transforms to collect
        transforms_list = []
        for sensor in self.sensors:
            transforms_list.extend(sensor['chain'])

        # https://stackoverflow.com/questions/31792680/how-to-make-values-in-list-of-dictionary-unique
        uniq_l = list(map(dict, frozenset(frozenset(i.items()) for i in transforms_list)))
        return uniq_l  # get unique values

    def createJSONFile(self, output_file, D):
        print("Saving the json output file to " + str(output_file) + ", please wait, it could take a while ...")
        f = open(output_file, 'w')
        json.encoder.FLOAT_REPR = lambda f: ("%.4f" % f)  # to get only four decimal places on the json file
        print >> f, json.dumps(D, indent=2, sort_keys=True)
        f.close()
        print("Completed.")

    @staticmethod
    def generateKey(parent, child, suffix=''):
        return parent + '-' + child + suffix

    @staticmethod
    def assertXMLSensorAttributes(xml_sensor):
        # Check if we have all the information needed. Abort if not.
        for attr in ['parent', 'calibration_parent', 'calibration_child', 'topic']:
            if not hasattr(xml_sensor, attr):
                raise ValueError(
                    'Element ' + attr + ' for sensor ' + xml_sensor.name + ' must be specified in the urdf/xacro.')