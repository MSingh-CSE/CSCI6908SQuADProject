# QANet on SQuAD 2.0

- PyTorch implementation of the paper [QANET: Combining Local Convolution with Global Self-Attention for Reading Comprehension](https://arxiv.org/pdf/1804.09541.pdf) by Adams Wei Yu, David Dohan, Minh-Thang Luong, Rui Zhao, Kai Chen, Mohammad Norouzi, Quoc V. Le

## Dependencies
```bash
conda env create -f environment.yml
```
```
conda activate squad
```

## Setup
1. This downloads SQuAD 2.0 training and dev sets, as well as the GloVe 300-dimensional word vectors (840B)
2. This also pre-processes the dataset for efficient data loading
```bash
python3 setup.py
```

## Training
CLI args training `args.py`
```bash
python train.py -n baseline --num_workers 4 --num_epochs 7 --eval_steps 50000 --batch_size 32 --hidden_size 64
```

To load the tensorboard
```bash
tensorboard --logdir save
```

## Testing
```bash
python test.py --split test --load_path save\train\{model_name}\best.pth.tar --name {test_name} --hidden_size 64
```

## Citation:

```
@article{DBLP:journals/corr/abs-1804-09541,
  author    = {Adams Wei Yu and
               David Dohan and
               Minh{-}Thang Luong and
               Rui Zhao and
               Kai Chen and
               Mohammad Norouzi and
               Quoc V. Le},
  title     = {QANet: Combining Local Convolution with Global Self-Attention for
               Reading Comprehension},
  journal   = {CoRR},
  volume    = {abs/1804.09541},
  year      = {2018},
  url       = {http://arxiv.org/abs/1804.09541},
  archivePrefix = {arXiv},
  eprint    = {1804.09541},
  timestamp = {Mon, 13 Aug 2018 16:48:18 +0200},
  biburl    = {https://dblp.org/rec/journals/corr/abs-1804-09541.bib},
  bibsource = {dblp computer science bibliography, https://dblp.org}
}

## Github Citing
``` https://github.com/abhirajtiwari/QANet