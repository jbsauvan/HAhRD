import sys
import os
import cPickle as pickle
import numpy as np
from scipy.spatial import cKDTree
import matplotlib.pyplot as plt

import uproot
import pandas as pd
import datetime
import concurrent.futures,multiprocessing
ncpu=multiprocessing.cpu_count()
executor=concurrent.futures.ThreadPoolExecutor(ncpu*4)

from main import get_subdet

#Location of the root data file
posfname='hex_pos_data/'
dfname='detector_data/hgcalNtuple_electrons_15GeV_n100.root'
#cfname='sq_cells_data/coef_dict_res_473,473_len_0.7.pkl'
sfname='sq_cells_data/sq_cells_dict_res_514,513_len_0.7.pkl'
resolution=(514,513)
edge_length=0.7

#Setting up the results directory
result_basepath='multicluster_results/'
if not os.path.exists(result_basepath):
    os.makedirs(result_basepath)

#Global Variable for Errors:
energy_diff=[]       #for error in energy of multicluster between hex and sq mesh
bary_x_diff=[]       #for error in x coordinate of barycenter of multicluster
bary_y_diff=[]       #for error in y coordinate of barycenter of multicluster
bary_z_diff=[]       #for error in z coordinate of barycenter of multicluster
event_mcl=[]         # for tracking the events-mcl pair which have
                                #error in properties more than 2%
                                #cuz all the error should have been same

############# HELPER FUNCTION ###############
def _get_layer_mask(all_hits_df,layer):
    subdet,eff_layer=get_subdet(layer)

    hit_subdet=(all_hits_df[['detid']].values>>25 & 0x7)==subdet
    hit_layer=(all_hits_df[['detid']].values>>19 & 0x1F)==eff_layer

    return hit_subdet & hit_layer

def readCoefFile(filename):
    fhandle=open(filename,'rb')
    coef_dict=pickle.load(fhandle)
    fhandle.close()

    return coef_dict

def readSqCellsDict(filename):
    fhandle=open(filename,'rb')
    sq_cells_dict=pickle.load(fhandle)
    fhandle.close()

    return sq_cells_dict

def readDataFile(filename):
    '''
    DESCRIPTION:
        This function will read the root file which contains the simulated
        data of particles and the corresponding recorded hits in the detector.
        The recorded hits in detertor will be later used for energy interpolation
        to the square cells.
        This code is similar to starting code in repo.
    USAGE:
        INPUT:
            filename    : the name of root file
        OUTPUT:
            df          : the pandas dataframe of the data in root file
    '''
    tree=uproot.open(filename)['ana/hgc']
    branches=[]
    branches += ["rechit_detid","rechit_z", "rechit_energy",
                'rechit_cluster2d','cluster2d_multicluster']
    cache={}
    df=tree.pandas.df(branches,cache=cache,executor=executor)

    return df

def check_event_multicluster_interpolation(event_id,df,sq_cells_dict,total_layers=40):
    branches=[]
    branches += ["rechit_detid","rechit_z", "rechit_energy",
                'rechit_cluster2d','cluster2d_multicluster']

    all_hits = pd.DataFrame({name.replace('rechit_',''):df.loc[event_id,name]
                            for name in branches if 'rechit_' in name })

    #Generating the multicluster index
    cl2d_idx=df.loc[event_id,'rechit_cluster2d']
    mcl_idx=df.loc[event_id,'cluster2d_multicluster'][cl2d_idx]

    #Adding it to all hits data frame
    all_hits['cluster3d'] = pd.Series(mcl_idx, index=all_hits.index)

    #Filtering the all hits based on one
    #No need here to separate the z>0 and z<0 since they will be
    #automatically separated in the multi-cluster

    print '#########################################'
    print '>>> Interpolation the event: ',event_id
    cluster_properties=interpolation_check(all_hits,sq_cells_dict,event_id,total_layers)

    print '>>> Not Writing the results to file in multicluster_results folder'
    # fname='multicluster_results/event%s.txt'%(event_id)
    # fhandle=open(fname,'w')
    # fhandle.write('####### Results for event %s ########\n'%(event_id))
    # for key,value in cluster_properties.iteritems():
    #     fhandle.write('\ncluster3d: %s \n'%key)
    #     #Printing the hex-cell values
    #     fhandle.write('initial val: %s,%s,%s,%s\n'%(value[0][0],value[0][1],value[0][2],value[0][3]))
    #     #Printing the mesh-cells value
    #     fhandle.write('sq_mesh val: %s,%s,%s,%s\n'%(value[1][0],value[1][1],value[1][2],value[1][3]))
    # fhandle.close()

############ MAIN FUNCTION ##################
def interpolation_check(all_hits_df,sq_cells_dict,event_id,total_layers):
    '''
    DESCRIPTION:
        This is mainly used to check the validity of the interpolation coef
        generated by out interpolaiton script.

        This function will read the event file from the data frame and
        use the interpolation coefficient generated by the previous
        script. Then we, go cluster by cluster in a particluar event to
        calculate the energy and barycenter of the cluster and match these
        properties between the hexagonal mesh and the square mesh.

        For each multicluster/cluster3d we have:
        (for both Hexagonal and Square Mesh)
            [energy             : the total energy of multicluster
             Xcenter_of_energy  : the x_cordinate of cluster's barycenter
             Ycenter_of_energy  : the y_cordinate of cluster's barycenter
             Zcenter of energy  : the z_cordinate of cluster's barycenter
             ]
    '''
    #To hold the required property of cluster for out Check
    cluster_properties={}

    #Iteratting layer by layer
    for layer in range(1,total_layers+1):
        #Finally starting the interpolation
        print '>>> Interpolating for Layer: %s'%(layer)

        #Filtering the current dataframe for this layers
        layer_hits=all_hits_df[_get_layer_mask(all_hits_df,layer)]
        if layer_hits.shape[0]==0:
            continue

        #Reading the coef_dict for this layer
        fname='sq_cells_data/coef_dict_layer_%s_res_%s,%s_len_%s.pkl'%(
                                layer,resolution[0],resolution[1],edge_length)
        coef_dict=readCoefFile(fname)

        #Reading the test_geometry file to get the hex_cells dict
        fname=posfname+'%s.pkl'%(layer)
        hex_pos=readCoefFile(fname)

        #Getting the center of the cells which have hits
        layer_z_arr=np.squeeze(layer_hits[['z']].values)
        detid_arr=np.squeeze(layer_hits[['detid']].values)
        cellid_arr=np.squeeze(layer_hits[['detid']].values) & 0x3FFFF
        energy_arr=np.squeeze(layer_hits[['energy']].values)
        cluster3d_arr=np.squeeze(layer_hits[['cluster3d']].values)

        #Reshaping if required
        if energy_arr.shape==():
            energy_arr=energy_arr.reshape((-1,))
            cellid_arr=cellid_arr.reshape((-1,))
            cluster3d_arr=cluster3d_arr.reshape((-1,))
            layer_z_arr=layer_z_arr.reshape((-1,))

        #Finally Calculating the Interpolation check values
        print '>>> Calculating the Multi-Cluster Properties'
        for hit_id in range(energy_arr.shape[0]):
            #print event_id,layer,hit_id,detid_arr[hit_id],cellid_arr[hit_id]
            #Identifying the corresponding hexagonal cell for this hit
            hex_cellid=cellid_arr[hit_id]

            #Retreiving the cell coordinates
            hex_cell_center=hex_pos[hex_cellid]
            #Retreiving the cell overlap coefs in sq grid/mesh
            overlaps=coef_dict[hex_cellid]

            #Adding the cluster field to dictionary
            cluster3d=cluster3d_arr[hit_id]
            if cluster3d not in cluster_properties.keys():
                init_list=np.array([0,0,0,0],dtype=np.float64)
                mesh_list=np.array([0,0,0,0],dtype=np.float64)
                cluster_properties[cluster3d]=[init_list,mesh_list]

            #Adding the hexagonal contribution to initial properties
            hit_energy=energy_arr[hit_id]
            hit_Wx=hex_cell_center[0]*hit_energy
            hit_Wy=hex_cell_center[1]*hit_energy
            hit_Wz=layer_z_arr[hit_id]*hit_energy
            init_list=[hit_energy,hit_Wx,hit_Wy,hit_Wz]
            cluster_properties[cluster3d][0]+=init_list

            #Adding mesh contribution to mesh properties
            norm_coef=np.sum([overlap[1] for overlap in overlaps])
            for overlap in overlaps:
                i,j=overlap[0]
                center=sq_cells_dict[(i,j)].center #*this is square cells center
                weight=(overlap[1]/norm_coef)

                mesh_energy=hit_energy*weight
                mesh_Wx=mesh_energy*center.coords[0][0]
                mesh_Wy=mesh_energy*center.coords[0][1]
                mesh_Wz=mesh_energy*layer_z_arr[hit_id]
                mesh_list=[mesh_energy,mesh_Wx,mesh_Wy,mesh_Wz]
                cluster_properties[cluster3d][1]+=mesh_list

    # energy_diff=[]
    # bary_x_diff=[]
    # bary_y_diff=[]
    # bary_z_diff=[]
    for key,value in cluster_properties.iteritems():
        #print '\ncluster3d: ',key
        #Finding the barycenter by dividing with total energy
        value[0][1:]=value[0][1:]/value[0][0]
        value[1][1:]=value[1][1:]/value[1][0]

        #Appending the differece to the list for plotting
        energy_diff.append(np.abs((value[0][0]-value[1][0])))
        bary_x_diff.append(np.abs((value[0][1]-value[1][1])))
        bary_y_diff.append(np.abs((value[0][2]-value[1][2])))
        bary_z_diff.append(np.abs((value[0][3]-value[1][3])))
        event_mcl.append((event_id,key))

        #Printing the hex-cell values
        #print 'initial val:',value[0][0],value[0][1],value[0][2],value[0][3]
        #Printing the mesh-cells value
        #print 'sq_mesh val:',value[1][0],value[1][1],value[1][2],value[1][3]


    return cluster_properties

def plot_error_histogram(energy_diff,bary_x_diff,bary_y_diff,bary_z_diff):
    #Plotting
    fig=plt.figure()
    fig.suptitle('Error (Absolute value) Histograms for %s clusters'%(
                                                        len(energy_diff)))

    #Adding the energy error histogram
    ax1=fig.add_subplot(221)
    ax1.set_ylabel('Count')
    ax1.set_xlabel('Absolute Diff in Energy')
    ax1.hist(energy_diff,bins=50)

    #Adding the Error in barycenter
    ax2=fig.add_subplot(222)
    ax2.set_ylabel('Count')
    ax2.set_xlabel('Absolute Diff in Barycenter-X')
    ax2.hist(bary_x_diff,bins=50)

    ax3=fig.add_subplot(223)
    ax3.set_ylabel('Count')
    ax3.set_xlabel('Absolute Diff in Barycenter-Y')
    ax3.hist(bary_y_diff,bins=50)

    ax4=fig.add_subplot(224)
    ax4.set_ylabel('Count')
    ax4.set_xlabel('Absolute Diff in Barycenter-Z')
    ax4.hist(bary_z_diff,bins=50)


    #plt.title('Absolute Error Histogram')
    # plt.rcParams["figure.figsize"]=(10,10)
    # plt.savefig('100.png')
    # plt.rcParams["figure.figsize"]=(10,200)
    # plt.savefig('200.png')
    # plt.rcParams["figure.figsize"]=(200,10)
    # plt.savefig('500.png')
    plt.show()

if __name__=='__main__':
    #Reading the datafile and coef_file
    df=readDataFile(dfname)
    sq_cells_dict=readSqCellsDict(sfname)

    #Now checking for ~100 events
    total_layers=40
    total_events=100
    event_ids=np.array(np.squeeze(df.index.tolist()))

    #Sampling some random events to interpolate
    #choice=np.random.choice(event_ids.shape[0],total_events)
    sample_event_ids=event_ids
    #sample_event_ids=[13]
    for i,event in enumerate(sample_event_ids):
        print i,' out of ',total_events
        check_event_multicluster_interpolation(event,df,sq_cells_dict)

    plot_error_histogram(energy_diff,bary_x_diff,bary_y_diff,bary_z_diff)

    for i in  range(len(event_mcl)):
        if (bary_y_diff[i]>0.02 or bary_x_diff[i]>0.02):#1e-4:
            print event_mcl[i],bary_y_diff[i],bary_x_diff[i]